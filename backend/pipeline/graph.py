"""Pipeline assembly: Extractor -> Auditor -> Rights_Checker -> Skeptic -> Writer (Req 2-7).

This module wires the pipeline nodes together, threading a single
:class:`~pipeline.state.PipelineState` through each node and maintaining the
per-node :class:`~pipeline.state.NodeStatus` transitions that drive live
progress (``completed/5``, Req 9).

Orchestrator choice — explicit sequential runner vs LangGraph
-------------------------------------------------------------
The design names LangGraph as the orchestrator. On this project's interpreter
(Python 3.14) LangGraph *does* resolve, but it drags in a heavy transitive tree
(langchain-core, langsmith, an alternate httpx, orjson, zstandard, ~25 packages)
while the pipeline needs none of its advanced features yet: the MVP is a linear
chain of pure/typed node functions with no branching, concurrency, or
checkpointing. Per the task guidance, we therefore implement an
**equivalently-structured explicit sequential runner** with the SAME
state-threading and the SAME insertion seams.

The runner is shaped so LangGraph can replace it with no change to node
contracts: each node is a callable ``(state, ...) -> state`` keyed by its
:class:`~config.NodeName`; the ordered node list is the only thing that changes
as Rights_Checker (task 12.3) and Skeptic (task 13.2) are inserted — both now
wired. Swapping in
``langgraph.graph.StateGraph`` later means registering these same node callables
as graph nodes and adding edges in this same order — the node functions
themselves are untouched.

Five-node progress semantics (Req 9)
------------------------------------
The pipeline is conceptually five nodes (Extractor, Auditor, Rights_Checker,
Skeptic, Writer). ``PipelineState.node_status`` is seeded with an entry for all
five so the SSE ``total:5`` denominator is stable across the MVP and later
stages. As of task 13.2 all five are REAL nodes: the Rights_Checker (task 12.3)
and the Skeptic (task 13.2) now genuinely transition RUNNING -> COMPLETED driven
by their actual node bodies, so a completed run reports ``5/5`` with no node
left as a pass-through and no change to the denominator.

Node-fatal vs node-local error handling (Req 9.4, 5.5; design "Node-fatal
propagation" and "Error Handling by Node")
--------------------------------------------------------------------
Extractor and Writer raise typed, node-fatal errors (``ExtractorError`` /
``WriterError``), each exposing ``to_state_error() -> {failedNode, message}``.
When such a node raises, the runner records ``state.error``, marks that node
:data:`NodeStatus.ERROR`, invokes the optional progress callback, and STOPS —
later nodes do not run. The Auditor is a pure function and is not expected to
raise for normal inputs; a defensive catch still converts an unexpected auditor
exception into the same node-fatal shape rather than crashing the background
task.

The Rights_Checker, by contrast, is NODE-LOCAL (Req 5.5): a retrieval failure,
empty result, or timeout must NOT stop the pipeline. ``run_rights_checker``
already absorbs those internally (it sets ``rights_status = UNAVAILABLE`` and
preserves the auditor Findings), so the node is wired via a dedicated
:func:`_run_node_local` wrapper that marks it RUNNING then COMPLETED and, should
``run_rights_checker`` itself raise unexpectedly, degrades gracefully to the
"unavailable" outcome WITHOUT setting ``state.error`` or halting. It is never
routed through the node-fatal path that Extractor/Writer use. The node always
reports COMPLETED (it completes even when the rights check is "unavailable"), so
the five-node progress still advances.

Knowledge base lifetime (build once; lazy; injectable)
------------------------------------------------------
The Rights_Checker needs a :class:`~rag.knowledge_base.KnowledgeBase`. Building
it loads and indexes the corpus, so it must be built ONCE and reused — never per
request, and never at import time (keeping ``import graph`` cheap and free of
corpus/network access). ``run_pipeline`` therefore accepts an optional ``kb``
parameter: when ``None`` it lazily builds (first call) and reuses a shared
module-level singleton via :func:`_get_knowledge_base`. Tests inject a stub ``kb``
(e.g. one whose ``search`` raises) to exercise the node-local degrade path
without touching the real corpus.

Progress callback (SSE seam, Req 9.2)
-------------------------------------
``run_pipeline`` accepts an optional ``progress`` callback invoked on every node
status transition (start and completion/error). Task 7.2's background task and
the later SSE endpoint (task 14.1) use it to emit ``progress`` / ``node_error``
events without this module importing any transport code.

Security note: the LLM API key is never read, logged, or stored here. The
injected ``provider`` holds the key; node error messages come from the node's
own ``to_state_error()`` and never include credentials.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Optional, Protocol

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
        from rag.knowledge_base import KnowledgeBase

        _KNOWLEDGE_BASE = KnowledgeBase()
    return _KNOWLEDGE_BASE


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


def _run_extractor(
    state: PipelineState, provider: LLMProvider, pdf_source: Any
) -> None:
    """Extractor node: PDF -> raw_text + ExtractedBill (Req 2).

    Populates ``state.raw_text`` and ``state.bill``. ``extract_bill`` reads the
    PDF and structures it; ``read_pdf_text`` is called again here only to record
    the raw text on the state for later inspection/debugging without changing
    the extractor's contract.
    """
    from pipeline.extractor import read_pdf_text

    # Record raw text first so it is available even if structuring fails. Reading
    # the PDF cannot raise the node-fatal ExtractorError by itself (that is
    # raised for empty text inside extract_bill), so this stays inside the node's
    # error boundary in _run_node.
    state.raw_text = read_pdf_text(pdf_source)
    state.bill = extract_bill(pdf_source, provider)


def _run_auditor(state: PipelineState, provider: LLMProvider) -> None:
    """Auditor node: compute Sum_Check then audit (Req 3, 4).

    Per the design, the Sum_Check is invoked at the START of the Auditor node.
    The Auditor is pure and takes no provider. Results land in
    ``state.sum_check`` and ``state.findings``. ``retained_findings`` is seeded
    here from the auditor findings as a PROVISIONAL value so the state is
    coherent if a later node is skipped; it is NOT authoritative. The
    Rights_Checker then APPENDS rights Findings to ``state.findings``, and the
    Skeptic node (task 13.2) is the sole authority that computes the final
    ``retained_findings = apply_skeptic(state.findings)`` the Writer consumes.
    """
    assert state.bill is not None  # Extractor ran first and succeeded.
    state.sum_check = compute_sum_check(state.bill)
    state.findings = audit(state.bill, state.sum_check)
    # Provisional seed only; the Skeptic node owns the final retained_findings.
    state.retained_findings = list(state.findings)


def _run_rights_checker(state: PipelineState, kb: "KnowledgeBase") -> None:
    """Rights_Checker node: append rights Findings via BM25 RAG (Req 5).

    Delegates to :func:`~pipeline.rights_checker.run_rights_checker`, which
    builds a query from the bill + auditor Findings, retrieves reference passages
    from ``kb`` within the 15s budget, and APPENDS any rights-issue Findings to
    ``state.findings`` while setting ``state.rights_status`` (COMPLETED, or
    UNAVAILABLE on failure/empty/timeout — Req 5.5). The auditor Findings are
    preserved.

    This node does NOT touch ``retained_findings``: the Skeptic node (task 13.2)
    is the sole authority on that field and computes it from the full
    (auditor + rights) ``state.findings`` immediately after this node runs. The
    provisional seed from the Auditor remains until the Skeptic overwrites it.

    This body is intentionally safe to run under :func:`_run_node_local`: it does
    not raise for the ordinary unavailable case (``run_rights_checker`` absorbs
    it), and any unexpected exception is converted to the unavailable outcome by
    the wrapper rather than halting the pipeline.
    """
    run_rights_checker(state, kb)


def _run_skeptic(state: PipelineState) -> None:
    """Skeptic node: filter ``findings`` into ``retained_findings`` (Req 6).

    The Skeptic is the SOLE authority on ``state.retained_findings``. It applies
    the pure :func:`~pipeline.skeptic.apply_skeptic` filter to the full
    (auditor + rights) ``state.findings`` and overwrites ``retained_findings``
    with the retained subset — dropping Findings below the 0.5 confidence bar or
    carrying zero line-item AND zero passage references (Req 6.2–6.5). The result
    is order-preserving and may be a strict subset of ``findings`` (e.g. a
    bill-total mismatch math-error, which carries no line-item/passage refs, is
    correctly dropped per Req 6.3).

    It calls no LLM, performs no I/O, and never mutates its inputs, so for the
    validated Findings produced upstream it does not raise for ordinary data.
    Running it through the normal :func:`_run_node` lifecycle is acceptable: an
    unexpected exception would be converted to the node-fatal shape, but a pure
    filter over already-validated Findings essentially never raises. Keeping it
    on :func:`_run_node` keeps the runner uniform (RUNNING -> COMPLETED) and the
    five-node progress genuine.
    """
    state.retained_findings = apply_skeptic(state.findings)


def _run_writer(state: PipelineState, provider: LLMProvider) -> None:
    """Writer node: Summary + Appeal_Letter in the session language (Req 7).

    Consumes ``retained_findings`` — the Skeptic-filtered subset of
    ``findings`` — and produces ``state.result`` with one summary entry per
    retained Finding (Req 7.1).
    """
    state.result = write_analysis(
        state.retained_findings, state.language, provider
    )


def _run_node(
    node: NodeName,
    body: Callable[[], None],
    state: PipelineState,
    progress: Optional[ProgressCallback],
) -> bool:
    """Run one node body under the shared status/error lifecycle.

    Marks the node RUNNING, runs ``body``, then marks COMPLETED. On a node-fatal
    error (a node exception exposing ``to_state_error()``, or any unexpected
    exception) it records ``state.error = {failedNode, message}``, marks the node
    ERROR, and returns ``False`` so the runner halts (Req 9.4). Returns ``True``
    on success.
    """
    _emit(progress, node, NodeStatus.RUNNING, state)
    try:
        body()
    except (ExtractorError, WriterError) as exc:
        # Typed node-fatal error: use the node's own {failedNode, message} shape.
        state.error = exc.to_state_error()
        _emit(progress, node, NodeStatus.ERROR, state)
        return False
    except Exception as exc:  # defensive: unexpected failure in any node
        state.error = {"failedNode": node.value, "message": str(exc)}
        _emit(progress, node, NodeStatus.ERROR, state)
        return False
    _emit(progress, node, NodeStatus.COMPLETED, state)
    return True


def _run_node_local(
    node: NodeName,
    body: Callable[[], None],
    state: PipelineState,
    progress: Optional[ProgressCallback],
) -> None:
    """Run a NODE-LOCAL node body: never halt, never set ``state.error`` (Req 5.5).

    Used for the Rights_Checker, whose failures are node-local by design: the
    pipeline must keep flowing to the Writer even when the rights check is
    unavailable. Marks the node RUNNING then COMPLETED. If ``body`` raises
    unexpectedly (``run_rights_checker`` already absorbs the ordinary
    failure/empty/timeout cases internally), the exception is caught and
    converted to the "unavailable" outcome — ``state.rights_status`` is set to
    :data:`RightsStatus.UNAVAILABLE` and the node is still marked COMPLETED — so
    the pipeline continues. This deliberately does NOT use the node-fatal path
    (``_run_node``) that records ``state.error`` and stops the runner.

    Unlike :func:`_run_node`, this returns ``None``: a node-local node never
    signals "halt", so the caller never branches on its outcome.
    """
    _emit(progress, node, NodeStatus.RUNNING, state)
    try:
        body()
    except Exception:  # node-local: degrade to unavailable, never halt (Req 5.5)
        state.rights_status = RightsStatus.UNAVAILABLE
    _emit(progress, node, NodeStatus.COMPLETED, state)


def run_pipeline(
    state: PipelineState,
    provider: LLMProvider,
    pdf_source: Any,
    progress: Optional[ProgressCallback] = None,
    kb: Optional["KnowledgeBase"] = None,
) -> PipelineState:
    """Run the pipeline end to end, mutating and returning ``state``.

    Threads ``state`` through Extractor -> Auditor -> Rights_Checker -> Skeptic
    -> Writer in order, keeping ``node_status`` current for all five conceptual
    nodes and surfacing progress via the optional ``progress`` callback. The
    pipeline halts on the first node-fatal error (Extractor/Writer), leaving
    ``state.error`` and the completed-node statuses intact so the frontend can
    show the failing node while retaining prior progress (Req 9.4). The
    Rights_Checker is node-local (Req 5.5): it never halts the pipeline and never
    sets ``state.error`` — a failure degrades to ``rights_status = UNAVAILABLE``
    and the Writer still runs.

    Retained-findings ownership: the Skeptic node is the SOLE authority on
    ``state.retained_findings``. The Auditor seeds a provisional value and the
    Rights_Checker appends to ``state.findings`` without touching it; the Skeptic
    then overwrites ``retained_findings = apply_skeptic(state.findings)`` (the
    confident, evidenced subset), and the Writer consumes that filtered set
    (Req 6, 7.1).

    Args:
        state: The session's pipeline state (session_id/language already set).
        provider: The injected LLM provider (holds credentials; never read here).
        pdf_source: Anything ``pdfplumber.open`` accepts (path or binary stream).
        progress: Optional callback fired on every node status transition.
        kb: Optional BM25 knowledge base for the Rights_Checker. When ``None``,
            the shared, lazily-built singleton (:func:`_get_knowledge_base`) is
            used so the corpus is indexed once and reused. Tests inject a stub
            (e.g. one whose ``search`` raises) to drive the node-local degrade
            path without the real corpus.

    Returns:
        The same ``state`` instance, now populated (or carrying ``error``).
    """
    _seed_node_status(state)

    # --- Extractor -------------------------------------------------------
    if not _run_node(
        NodeName.EXTRACTOR,
        lambda: _run_extractor(state, provider, pdf_source),
        state,
        progress,
    ):
        return state

    # --- Auditor (Sum_Check runs at the start of this node) --------------
    if not _run_node(
        NodeName.AUDITOR,
        lambda: _run_auditor(state, provider),
        state,
        progress,
    ):
        return state

    # --- Rights_Checker (task 12.3) --------------------------------------
    # Node-local (Req 5.5): a retrieval failure/empty/timeout must NOT halt the
    # pipeline. ``run_rights_checker`` absorbs those internally (unavailable
    # status, auditor Findings preserved); ``_run_node_local`` additionally
    # degrades any unexpected exception to the unavailable outcome without
    # setting ``state.error``. The KB is built once and reused (or injected).
    resolved_kb = kb if kb is not None else _get_knowledge_base()
    _run_node_local(
        NodeName.RIGHTS_CHECKER,
        lambda: _run_rights_checker(state, resolved_kb),
        state,
        progress,
    )

    # --- Skeptic (task 13.2) ---------------------------------------------
    # The Skeptic is the sole authority on ``retained_findings``: it filters the
    # full (auditor + rights) ``findings`` down to the confident, evidenced
    # subset the Writer consumes (Req 6). It is a pure filter (no LLM, no I/O),
    # so running it on the normal node lifecycle (RUNNING -> COMPLETED) is safe —
    # it does not raise for the validated Findings produced upstream. If it ever
    # did, ``_run_node`` would surface it as a node-fatal error rather than crash
    # the background task, which is acceptable for an unexpected failure.
    if not _run_node(
        NodeName.SKEPTIC,
        lambda: _run_skeptic(state),
        state,
        progress,
    ):
        return state

    # --- Writer ----------------------------------------------------------
    if not _run_node(
        NodeName.WRITER,
        lambda: _run_writer(state, provider),
        state,
        progress,
    ):
        return state

    return state
