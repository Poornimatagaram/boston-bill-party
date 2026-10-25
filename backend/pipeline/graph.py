"""Pipeline assembly: Extractor -> Auditor -> Rights_Checker -> Skeptic -> Writer (Req 2-7).

The pipeline is a compiled LangGraph ``StateGraph`` whose state schema is
:class:`~pipeline.state.PipelineState` itself. ``run_pipeline`` keeps its
pre-migration signature, so ``sessions.py`` and the eval harness are unchanged.

Design
------
* Five nodes keyed by lowercase :class:`~config.NodeName`, wired
  ``START -> extractor -> auditor -> rights_checker -> skeptic -> writer -> END``.
  Each node is a thin wrapper around the unchanged Node_Function(s); bodies run
  on a deep copy of the state and return the changed fields as a partial update.
* Node-fatal nodes (Extractor, Auditor, Skeptic, Writer) never raise into
  LangGraph: an ``ExtractorError``/``WriterError`` becomes
  ``to_state_error()``, any other exception becomes
  ``{failedNode, message: str(exc)}``, and the Error_Router (conditional edges)
  sends the graph to ``END`` whenever ``state.error`` is set (Req 5).
* The Rights_Checker is node-local: any failure degrades to
  ``rights_status = UNAVAILABLE``, the node still completes, ``error`` stays
  ``None``, and a plain edge continues to the Skeptic (Req 6).
* The provider, knowledge base, PDF source, progress callback, and the
  caller's live ``PipelineState`` travel in :class:`PipelineContext` via
  LangGraph runtime context, so none of them ever enter Graph_State (Req 3.8).
* Progress: each wrapper emits RUNNING before its body and COMPLETED/ERROR after
  it, synchronously on the calling (worker) thread, after mirroring its update
  onto the caller's live state. Event order matches the pre-migration runner
  (Req 7). Callback exceptions are swallowed.
* The graph is compiled once per process, lazily and lock-guarded, with no
  checkpointer (Req 4.5). Importing this module builds nothing: no graph, no
  knowledge base, no provider, no network (Req 4.7).

The frozen pre-migration sequential runner lives in
``pipeline/_reference_runner.py`` and is used only by the equivalence tests.

Security note: the LLM API key is never read, logged, or stored here. The
injected ``provider`` holds the key; node error messages come from the node's
own ``to_state_error()`` and never include credentials.
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional, Protocol

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from config import NodeName
from models import RightsStatus
from pipeline.auditor import audit
from pipeline.extractor import ExtractorError, extract_bill
from pipeline.rights_checker import run_rights_checker
from pipeline.skeptic import apply_skeptic
from pipeline.state import NodeStatus, PipelineState
from pipeline.sumcheck import compute_sum_check
from pipeline.writer import WriterError, write_analysis

if TYPE_CHECKING:  # import only for typing; never at runtime (keeps import cheap)
    from langgraph.graph.state import CompiledStateGraph

    from rag.knowledge_base import KnowledgeBase

# Canonical ordering of the five conceptual nodes. Progress denominators and the
# seeded ``node_status`` map use this list so ``completed/5`` is stable (Req 9).
NODE_ORDER: tuple[NodeName, ...] = (
    NodeName.EXTRACTOR,
    NodeName.AUDITOR,
    NodeName.RIGHTS_CHECKER,
    NodeName.SKEPTIC,
    NodeName.WRITER,
)

# Total node count for SSE ``total`` (design "SSE Event Shapes": total: 5).
TOTAL_NODES: int = len(NODE_ORDER)


class LLMProvider(Protocol):
    """Minimal protocol for the injected provider (mirrors ``llm/provider.py``).

    Kept local so the graph imports without a hard dependency on the concrete
    provider implementation and stays testable with a mock. The provider holds
    the credentials; the graph never touches them.
    """

    def complete(
        self, *, system: str, user: str, timeout_s: float, node: Any
    ) -> str: ...


# A progress callback is invoked as ``progress(node_name, status, state)`` on
# every node status transition. ``node_name`` is the lowercase NodeName value.
ProgressCallback = Callable[[str, NodeStatus, PipelineState], None]


# Lazily-built, cached BM25 knowledge base for the Rights_Checker. Built ONCE on
# first use and reused across requests (building loads/indexes the corpus). It is
# intentionally NOT built at import time so ``import graph`` stays cheap and does
# no corpus/network access; ``_get_knowledge_base`` constructs it on demand.
_KNOWLEDGE_BASE: Optional["KnowledgeBase"] = None
# Guards the first-call build so two worker threads racing on the first run
# cannot construct the KB twice (double-checked init).
_KNOWLEDGE_BASE_LOCK = threading.Lock()


def _get_knowledge_base() -> "KnowledgeBase":
    """Return the shared :class:`KnowledgeBase`, building it once on first use.

    The ``rag.knowledge_base`` import is deferred to the function body so that
    importing this module never loads ``rank_bm25`` or reads the corpus — the KB
    is built the first time a pipeline run actually needs it and cached for every
    subsequent run (Req 5.1: build once, reuse). Tests that want to avoid the
    real corpus inject their own ``kb`` into :func:`run_pipeline` and never reach
    this accessor.
    """
    global _KNOWLEDGE_BASE
    if _KNOWLEDGE_BASE is None:
        with _KNOWLEDGE_BASE_LOCK:
            if _KNOWLEDGE_BASE is None:
                from rag.knowledge_base import KnowledgeBase

                _KNOWLEDGE_BASE = KnowledgeBase()
    return _KNOWLEDGE_BASE


# ---------------------------------------------------------------------------
# LangGraph migration helpers (runtime context, state conversion, updates).
#
# Building blocks used by the node wrappers and ``run_pipeline``. They are
# plain Python (no LangGraph calls), and nothing in this module imports
# ``langgraph.checkpoint``.
# ---------------------------------------------------------------------------

# The 11 Graph_State fields, in declaration order. Graph_State is
# ``PipelineState`` itself (design decision 1), so this is the single source of
# truth for which keys may cross the graph boundary (Req 2.1).
_STATE_FIELDS: tuple[str, ...] = tuple(PipelineState.model_fields)


@dataclass(frozen=True)
class PipelineContext:
    """Per-run dependencies injected via LangGraph runtime context.

    Passed as ``graph.invoke(..., context=PipelineContext(...))`` and declared as
    the graph's ``context_schema``. It is never part of Graph_State and never
    checkpointed, so the provider (which holds the API key) and the knowledge
    base never enter the state (Req 3.8).

    ``live_state`` is the caller's own ``PipelineState`` instance. Node wrappers
    mirror each update onto it so ``Session.progress`` and the Progress_Callback
    see live status while the graph runs.
    """

    provider: LLMProvider
    kb: "KnowledgeBase"
    pdf_source: Any
    live_state: PipelineState
    progress: Optional[ProgressCallback]


def to_graph_input(state: PipelineState) -> dict[str, Any]:
    """Return the graph input for ``state``: exactly the 11 fields, deep-copied.

    Deep copies keep the graph's working values independent of the caller's
    instance, so nothing the graph does can alias into the caller except through
    the explicit mirroring in :func:`_apply_update`. ``Decimal`` values keep
    their value and scale under ``deepcopy`` (Req 2.5).
    """
    return {name: copy.deepcopy(getattr(state, name)) for name in _STATE_FIELDS}


def from_graph_state(
    values: Mapping[str, Any], caller: Optional[PipelineState] = None
) -> PipelineState:
    """Convert final Graph_State ``values`` into a validated ``PipelineState``.

    LangGraph may omit keys that were never written (fields left at a ``None``
    default). Omitted keys are filled from ``caller`` (deep-copied) when given,
    otherwise from the ``PipelineState`` defaults. Unknown keys are passed
    through to validation so ``extra="forbid"`` rejects them, and validation is
    strict (no type coercion, so no ``float`` sneaks in for money).

    Raises:
        pydantic.ValidationError: for an unknown key or a value of the wrong
            type (Req 2.7). ``caller`` is only read, never modified, so a
            failed conversion leaves the caller's instance unchanged.
    """
    merged: dict[str, Any] = {}
    if caller is not None:
        for name in _STATE_FIELDS:
            if name not in values:
                merged[name] = copy.deepcopy(getattr(caller, name))
    merged.update(values)
    return PipelineState.model_validate(merged, strict=True)


def _diff(before: PipelineState, after: PipelineState) -> dict[str, Any]:
    """Return ``{field: value}`` for fields whose value changed (by ``==``).

    ``after`` is the node's private deep-copied working state, so its values can
    be returned directly without aliasing the graph's previous state. Partial
    writes made before a node failure are captured too (design decision 3).
    """
    return {
        name: getattr(after, name)
        for name in _STATE_FIELDS
        if getattr(before, name) != getattr(after, name)
    }


def _apply_update(live: PipelineState, update: Mapping[str, Any]) -> None:
    """Validate ``update`` against ``PipelineState``, then ``setattr`` onto ``live``.

    The candidate (``live``'s current fields overlaid by ``update``) is fully
    validated first, so an unknown key or wrongly-typed value raises
    ``pydantic.ValidationError`` before any attribute on ``live`` changes.
    Validated values are then assigned field by field, preserving ``live``'s
    object identity.
    """
    candidate = {name: getattr(live, name) for name in _STATE_FIELDS}
    candidate.update(update)
    validated = PipelineState.model_validate(candidate, strict=True)
    for name in update:
        setattr(live, name, getattr(validated, name))


def _emit(
    progress: Optional[ProgressCallback],
    node: NodeName,
    status: NodeStatus,
    state: PipelineState,
) -> None:
    """Record ``status`` for ``node`` on the state and notify the callback.

    Centralizes the two things every transition must do: persist the status in
    ``state.node_status`` (so a late subscriber can read the current picture) and
    fire the optional progress callback (so the SSE layer can stream it). A
    callback exception must never corrupt the pipeline, so it is swallowed
    defensively — progress is best-effort telemetry, not pipeline logic.
    """
    state.node_status[node.value] = status
    if progress is not None:
        try:
            progress(node.value, status, state)
        except Exception:  # best-effort telemetry; never fail the pipeline
            pass


def _seed_node_status(state: PipelineState) -> None:
    """Initialize every conceptual node to PENDING (idempotent)."""
    for node in NODE_ORDER:
        state.node_status.setdefault(node.value, NodeStatus.PENDING)


# ---------------------------------------------------------------------------
# LangGraph node bodies and wrappers (task 3.4).
#
# Each body runs against the wrapper's private deep-copied working state and
# makes the same calls, in the same order, as the pre-migration ``_run_*``
# helper (see ``pipeline/_reference_runner.py``). Node_Functions are looked up through this module's globals at call
# time, so tests that monkeypatch ``pipeline.graph.audit`` etc. take effect.
# ---------------------------------------------------------------------------

NodeBody = Callable[[PipelineState, PipelineContext], None]
GraphNode = Callable[[PipelineState, "Runtime[PipelineContext]"], dict[str, Any]]


def _extractor_body(w: PipelineState, ctx: PipelineContext) -> None:
    """Extractor: ``raw_text = read_pdf_text(pdf)`` then ``bill = extract_bill(...)`` (Req 3.9)."""
    # Module-attribute lookup at call time, as in the pre-migration runner, so
    # ``monkeypatch.setattr(extractor_mod, "read_pdf_text", ...)`` still applies.
    from pipeline.extractor import read_pdf_text

    w.raw_text = read_pdf_text(ctx.pdf_source)
    w.bill = extract_bill(ctx.pdf_source, ctx.provider)


def _auditor_body(w: PipelineState, ctx: PipelineContext) -> None:
    """Auditor: Sum_Check first, then audit; seed a distinct retained list (Req 3.3, 3.6).

    Pure: never touches ``ctx.provider``.
    """
    assert w.bill is not None  # Extractor ran first and succeeded.
    w.sum_check = compute_sum_check(w.bill)
    w.findings = audit(w.bill, w.sum_check)
    w.retained_findings = list(w.findings)


def _rights_checker_body(w: PipelineState, ctx: PipelineContext) -> None:
    """Rights_Checker: ``run_rights_checker(w, kb)`` exactly once (Req 3.10)."""
    run_rights_checker(w, ctx.kb)


def _skeptic_body(w: PipelineState, ctx: PipelineContext) -> None:
    """Skeptic: ``retained_findings = apply_skeptic(findings)`` (Req 3.4, 3.6).

    Pure: never touches ``ctx.provider`` and leaves ``findings`` unchanged.
    """
    w.retained_findings = apply_skeptic(w.findings)


def _writer_body(w: PipelineState, ctx: PipelineContext) -> None:
    """Writer: ``write_analysis`` exactly once with the retained findings (Req 3.5)."""
    w.result = write_analysis(w.retained_findings, w.language, ctx.provider)


def _finish_node(
    node: NodeName,
    state: PipelineState,
    working: PipelineState,
    status: NodeStatus,
    error: Optional[dict[str, Any]],
    ctx: PipelineContext,
) -> dict[str, Any]:
    """Build the node's update, mirror it onto ``live_state``, then emit ``status``.

    The diff keeps partial writes made before a failure (design decision 3).
    Mirroring happens before the final emit so the Progress_Callback sees the
    node's writes on the caller's instance, as with the pre-migration runner.
    """
    update = _diff(state, working)
    update["node_status"] = {**state.node_status, node.value: status}
    if error is not None:
        update["error"] = error
    _apply_update(ctx.live_state, update)
    _emit(ctx.progress, node, status, ctx.live_state)
    return update


def _fatal_node(node: NodeName, body: NodeBody) -> GraphNode:
    """Build a node-fatal wrapper (extractor, auditor, skeptic, writer).

    Lifecycle: emit RUNNING on the live state, run ``body`` on a deep copy, map
    ``ExtractorError``/``WriterError`` to ``to_state_error()`` and any other
    exception to ``{failedNode, message: str(exc)}`` (Req 5.1-5.3), mirror the
    update onto the live state, emit the final status, and return the update.
    Body exceptions never escape, so LangGraph never sees them (Req 5.7).
    """

    def wrapper(
        state: PipelineState, runtime: "Runtime[PipelineContext]"
    ) -> dict[str, Any]:
        ctx = runtime.context
        _emit(ctx.progress, node, NodeStatus.RUNNING, ctx.live_state)  # Req 7.1
        working = state.model_copy(deep=True)
        working.node_status[node.value] = NodeStatus.RUNNING
        error: Optional[dict[str, Any]] = None
        try:
            body(working, ctx)
            status = NodeStatus.COMPLETED
        except (ExtractorError, WriterError) as exc:
            status, error = NodeStatus.ERROR, exc.to_state_error()
        except Exception as exc:  # defensive: unexpected failure in any node
            status = NodeStatus.ERROR
            error = {"failedNode": node.value, "message": str(exc)}
        return _finish_node(node, state, working, status, error, ctx)

    wrapper.__name__ = f"{node.value}_node"
    wrapper.__qualname__ = wrapper.__name__
    return wrapper


def _local_node(node: NodeName, body: NodeBody) -> GraphNode:
    """Build the node-local wrapper (rights_checker, Req 6.1, 6.2, 7.4).

    Same lifecycle as :func:`_fatal_node`, except any body exception degrades to
    ``rights_status = UNAVAILABLE``; the final status is always COMPLETED and
    ``error`` is never written. Partial appends made before the exception are
    kept, matching the pre-migration runner.
    """

    def wrapper(
        state: PipelineState, runtime: "Runtime[PipelineContext]"
    ) -> dict[str, Any]:
        ctx = runtime.context
        _emit(ctx.progress, node, NodeStatus.RUNNING, ctx.live_state)
        working = state.model_copy(deep=True)
        working.node_status[node.value] = NodeStatus.RUNNING
        try:
            body(working, ctx)
        except Exception:  # node-local: degrade to unavailable, never halt
            working.rights_status = RightsStatus.UNAVAILABLE
        return _finish_node(node, state, working, NodeStatus.COMPLETED, None, ctx)

    wrapper.__name__ = f"{node.value}_node"
    wrapper.__qualname__ = wrapper.__name__
    return wrapper


# The five graph node callables keyed by NodeName, in NODE_ORDER (task 3.5 wires them).
_NODE_WRAPPERS: dict[NodeName, GraphNode] = {
    NodeName.EXTRACTOR: _fatal_node(NodeName.EXTRACTOR, _extractor_body),
    NodeName.AUDITOR: _fatal_node(NodeName.AUDITOR, _auditor_body),
    NodeName.RIGHTS_CHECKER: _local_node(NodeName.RIGHTS_CHECKER, _rights_checker_body),
    NodeName.SKEPTIC: _fatal_node(NodeName.SKEPTIC, _skeptic_body),
    NodeName.WRITER: _fatal_node(NodeName.WRITER, _writer_body),
}


# ---------------------------------------------------------------------------
# Graph topology, Error_Router, and compile-once accessor (task 3.5).
#
# Nothing here runs at import time: the builder and the compiled graph are
# created on the first ``get_compiled_graph()`` call (Req 4.5, 4.7). The default
# graph has no checkpointer, and this module never imports ``langgraph.checkpoint``.
# ---------------------------------------------------------------------------


def _route_after(node: NodeName) -> Callable[[PipelineState], str]:
    """Return the Error_Router for ``node`` (Req 5.4-5.6).

    The router sends the graph to ``END`` when ``state.error`` is set (a
    node-fatal failure), otherwise to the next node in ``NODE_ORDER`` (or
    ``END`` after the last node). It is a pure function of ``state.error``.
    """
    index = NODE_ORDER.index(node)
    successor = NODE_ORDER[index + 1].value if index + 1 < len(NODE_ORDER) else END

    def route(state: PipelineState) -> str:
        return END if state.error is not None else successor

    route.__name__ = f"route_after_{node.value}"
    route.__qualname__ = route.__name__
    return route


def _build_graph() -> StateGraph:
    """Build the (uncompiled) Pipeline_Graph builder (Req 3.1, 4.1-4.4).

    Five nodes keyed by lowercase ``NodeName``; ``START -> extractor``; the
    Error_Router on every node-fatal node; a plain ``rights_checker -> skeptic``
    edge because the Rights_Checker is node-local and never fatal (Req 5.9).
    """
    builder = StateGraph(PipelineState, context_schema=PipelineContext)
    for node in NODE_ORDER:
        builder.add_node(node.value, _NODE_WRAPPERS[node])

    builder.add_edge(START, NodeName.EXTRACTOR.value)
    for node, nxt in (
        (NodeName.EXTRACTOR, NodeName.AUDITOR),
        (NodeName.AUDITOR, NodeName.RIGHTS_CHECKER),
        (NodeName.SKEPTIC, NodeName.WRITER),
    ):
        builder.add_conditional_edges(
            node.value, _route_after(node), {nxt.value: nxt.value, END: END}
        )
    builder.add_edge(NodeName.RIGHTS_CHECKER.value, NodeName.SKEPTIC.value)
    # Both writer outcomes end the run; routing still goes through the
    # Error_Router so Req 5.2 holds literally.
    builder.add_conditional_edges(
        NodeName.WRITER.value, _route_after(NodeName.WRITER), {END: END}
    )
    return builder


# Compiled default graph, built lazily on first use and shared process-wide.
_COMPILED: Optional["CompiledStateGraph"] = None
_COMPILED_LOCK = threading.Lock()


def get_compiled_graph() -> "CompiledStateGraph":
    """Return the default compiled Pipeline_Graph, compiling it once (Req 4.5).

    Double-checked locking so concurrent first calls from worker threads compile
    exactly once. No checkpointer: all per-run data lives in the ``invoke``
    input and the ``PipelineContext``, so concurrent runs share no mutable state
    (Req 4.6).
    """
    global _COMPILED
    if _COMPILED is None:
        with _COMPILED_LOCK:
            if _COMPILED is None:
                _COMPILED = _build_graph().compile()
    return _COMPILED




def run_pipeline(
    state: PipelineState,
    provider: LLMProvider,
    pdf_source: Any,
    progress: Optional[ProgressCallback] = None,
    kb: Optional["KnowledgeBase"] = None,
) -> PipelineState:
    """Run the pipeline end to end on the compiled graph, returning ``state``.

    Seeds ``node_status`` (all five PENDING), resolves the knowledge base, then
    invokes the compiled Pipeline_Graph with the provider, KB, PDF source,
    progress callback, and ``state`` (as the live instance) injected through
    :class:`PipelineContext`. Node wrappers mirror every update onto ``state``
    and fire ``progress`` synchronously on this thread, so live progress works
    as before (Req 7.1-7.5).

    A node-fatal failure ends the run at that node with ``state.error`` set; it
    is never raised to the caller (Req 5.7). A Rights_Checker failure degrades to
    ``rights_status = UNAVAILABLE`` and the run continues (Req 6).

    Args:
        state: The session's pipeline state (session_id/language already set).
        provider: The injected LLM provider (holds credentials; never read here).
        pdf_source: Anything ``pdfplumber.open`` accepts (path or binary stream).
        progress: Optional callback fired on every node status transition.
        kb: Optional knowledge base for the Rights_Checker. When ``None``, the
            shared lazily-built singleton (:func:`_get_knowledge_base`) is used.
            It is resolved before the graph runs, so a KB build failure raises
            here (outside any node) and the Session_Bridge reports it (Req 7.9).

    Returns:
        The same ``state`` instance (identity preserved, Req 2.3, 2.6), with all
        11 fields set to the final Graph_State values.

    Raises:
        pydantic.ValidationError: if the final Graph_State fails validation; the
            caller's fields are then left as the wrappers last mirrored them,
            untouched by the failed conversion (Req 2.7).
    """
    _seed_node_status(state)
    resolved_kb = kb if kb is not None else _get_knowledge_base()
    ctx = PipelineContext(
        provider=provider,
        kb=resolved_kb,
        pdf_source=pdf_source,
        live_state=state,
        progress=progress,
    )
    final = get_compiled_graph().invoke(to_graph_input(state), context=ctx)
    # Validate fully before assigning anything onto the caller's instance.
    validated = from_graph_state(final, state)
    for name in _STATE_FIELDS:
        setattr(state, name, getattr(validated, name))
    return state
