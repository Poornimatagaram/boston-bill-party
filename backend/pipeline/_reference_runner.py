"""Frozen Pre_Migration_Runner: the explicit sequential pipeline runner (TESTS ONLY).

This module is a verbatim snapshot of the orchestration in ``pipeline/graph.py``
as it stood before the LangGraph migration (langgraph-migration spec, task 1.2).
It exists solely as the reference model for the equivalence tests
(Properties 1 and 2): ``run_pipeline_reference`` is compared against the new
LangGraph-backed ``pipeline.graph.run_pipeline`` on identical inputs.

Rules for this file:

- Do NOT edit the orchestration logic. It must keep matching the pre-migration
  behavior so equivalence tests stay meaningful.
- It imports the SAME Node_Functions from their existing modules, so the tests
  compare orchestration only, never node logic.
- ``read_pdf_text`` is looked up from ``pipeline.extractor`` at call time (as in
  the original), so ``monkeypatch.setattr(extractor_mod, "read_pdf_text", ...)``
  affects both runners identically.
- Production code must never import this module. The leading underscore keeps
  pytest from collecting it.

The only change from the original is the rename ``run_pipeline`` ->
``run_pipeline_reference``. The KB singleton is a separate copy so this module
does not depend on ``pipeline.graph`` internals that the migration rewrites.
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

# Canonical ordering of the five conceptual nodes (same as pre-migration).
NODE_ORDER: tuple[NodeName, ...] = (
    NodeName.EXTRACTOR,
    NodeName.AUDITOR,
    NodeName.RIGHTS_CHECKER,
    NodeName.SKEPTIC,
    NodeName.WRITER,
)

TOTAL_NODES: int = len(NODE_ORDER)


class LLMProvider(Protocol):
    """Minimal protocol for the injected provider (mirrors ``llm/provider.py``)."""

    def complete(
        self, *, system: str, user: str, timeout_s: float, node: Any
    ) -> str: ...


# ``progress(node_name, status, state)`` on every node status transition.
ProgressCallback = Callable[[str, NodeStatus, PipelineState], None]


_KNOWLEDGE_BASE: Optional["KnowledgeBase"] = None


def _get_knowledge_base() -> "KnowledgeBase":
    """Return the shared :class:`KnowledgeBase`, building it once on first use."""
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
    """Record ``status`` for ``node`` on the state and notify the callback."""
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
    """Extractor node: PDF -> raw_text + ExtractedBill (Req 2)."""
    from pipeline.extractor import read_pdf_text

    # Record raw text first so it is available even if structuring fails.
    state.raw_text = read_pdf_text(pdf_source)
    state.bill = extract_bill(pdf_source, provider)


def _run_auditor(state: PipelineState, provider: LLMProvider) -> None:
    """Auditor node: compute Sum_Check then audit (Req 3, 4)."""
    assert state.bill is not None  # Extractor ran first and succeeded.
    state.sum_check = compute_sum_check(state.bill)
    state.findings = audit(state.bill, state.sum_check)
    # Provisional seed only; the Skeptic node owns the final retained_findings.
    state.retained_findings = list(state.findings)


def _run_rights_checker(state: PipelineState, kb: "KnowledgeBase") -> None:
    """Rights_Checker node: append rights Findings via BM25 RAG (Req 5)."""
    run_rights_checker(state, kb)


def _run_skeptic(state: PipelineState) -> None:
    """Skeptic node: filter ``findings`` into ``retained_findings`` (Req 6)."""
    state.retained_findings = apply_skeptic(state.findings)


def _run_writer(state: PipelineState, provider: LLMProvider) -> None:
    """Writer node: Summary + Appeal_Letter in the session language (Req 7)."""
    state.result = write_analysis(
        state.retained_findings, state.language, provider
    )


def _run_node(
    node: NodeName,
    body: Callable[[], None],
    state: PipelineState,
    progress: Optional[ProgressCallback],
) -> bool:
    """Run one node body under the shared status/error lifecycle (node-fatal)."""
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
    """Run a NODE-LOCAL node body: never halt, never set ``state.error`` (Req 5.5)."""
    _emit(progress, node, NodeStatus.RUNNING, state)
    try:
        body()
    except Exception:  # node-local: degrade to unavailable, never halt (Req 5.5)
        state.rights_status = RightsStatus.UNAVAILABLE
    _emit(progress, node, NodeStatus.COMPLETED, state)


def run_pipeline_reference(
    state: PipelineState,
    provider: LLMProvider,
    pdf_source: Any,
    progress: Optional[ProgressCallback] = None,
    kb: Optional["KnowledgeBase"] = None,
) -> PipelineState:
    """Pre-migration ``run_pipeline``: mutate and return ``state`` (reference model)."""
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

    # --- Rights_Checker (node-local) -------------------------------------
    resolved_kb = kb if kb is not None else _get_knowledge_base()
    _run_node_local(
        NodeName.RIGHTS_CHECKER,
        lambda: _run_rights_checker(state, resolved_kb),
        state,
        progress,
    )

    # --- Skeptic ---------------------------------------------------------
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
