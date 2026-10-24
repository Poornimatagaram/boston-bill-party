"""Focused tests for Rights_Checker wiring in the pipeline (task 12.3, Req 5.1, 5.5).

These exercise ``run_pipeline`` end to end (Extractor -> Auditor -> Rights_Checker
-> Writer) with a deterministic stubbed LLM provider and an INJECTED knowledge
base, so no network, no real LLM, and no real corpus are involved. Two behaviors
are pinned:

* With a KB that returns a strongly-matching passage, the Rights_Checker appends
  a ``rights_issue`` Finding to ``state.findings`` (carried through to the Writer
  via ``retained_findings``) and sets ``state.rights_status = COMPLETED``.
* With a KB whose ``search`` RAISES, the Rights_Checker is node-local (Req 5.5):
  it does NOT halt the pipeline — ``state.error`` stays ``None``, the status is
  ``UNAVAILABLE``, the Writer still runs, and ``state.result`` is produced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

import pipeline.extractor as extractor_mod
from models import FindingType, RightsStatus, SupportedLanguage
from pipeline.graph import run_pipeline
from pipeline.state import NodeStatus, PipelineState

# A synthetic bill text + matching Extractor JSON. Two identical code+description
# lines drive a duplicate-charge Finding; stated_total (250.00) != sum of amounts
# (200.00) drives a math-error Finding. This gives the Rights_Checker a non-empty
# query to retrieve against.
_BILL_TEXT = (
    "GENERAL HOSPITAL — SYNTHETIC TEST BILL\n"
    "99213 Office visit  qty 1  unit 100.00  amount 100.00\n"
    "99213 Office visit  qty 1  unit 50.00   amount 50.00\n"
    "85025 Blood panel   qty 1  unit 50.00   amount 50.00\n"
    "Total due: 250.00\n"
)

_EXTRACTOR_JSON = json.dumps(
    {
        "provider_name": "General Hospital",
        "provider_address": None,
        "account_number": "SYN-0001",
        "line_items": [
            {"id": "l1", "description": "Office visit", "billing_code": "99213",
             "quantity": 1, "unit_price": 100.00, "amount": 100.00},
            {"id": "l2", "description": "Office visit", "billing_code": "99213",
             "quantity": 1, "unit_price": 50.00, "amount": 50.00},
            {"id": "l3", "description": "Blood panel", "billing_code": "85025",
             "quantity": 1, "unit_price": 50.00, "amount": 50.00},
        ],
        "stated_total": 250.00,
    }
)

_WRITER_SENTENCE = "This charge appears to be a billing error you can contest."


class _StubProvider:
    """Deterministic in-memory LLM stand-in — no network, no credentials."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, *, system: str, user: str, timeout_s: float, node) -> str:
        node_id = getattr(node, "value", node)
        self.calls.append(str(node_id))
        if node_id == "extractor":
            return _EXTRACTOR_JSON
        if node_id == "writer":
            return _WRITER_SENTENCE
        raise AssertionError(f"Unexpected provider call from node {node_id!r}")


@dataclass(frozen=True)
class _StubResult:
    """Structural stand-in for ``rag.knowledge_base.SearchResult``."""

    id: str
    text: str
    source: str
    score: float


class _MatchingKB:
    """A KB whose ``search`` returns one strongly-matching passage.

    The large raw score (100.0) clears the Rights_Checker's normalization
    threshold (``s/(s+K) >= 0.7`` needs ``s >= ~9.33``), so the passage is
    recorded as a rights-issue Finding.
    """

    def __init__(self) -> None:
        self.queries: list[str] = []

    def search(self, query: str, k: int = 10) -> list[_StubResult]:
        self.queries.append(query)
        return [
            _StubResult(
                id="ma-duplicate-charges",
                text="Duplicate line items that repeat the same billing code "
                "and description may indicate a billing error.",
                source="MA Patient Billing Rights",
                score=100.0,
            )
        ]


class _RaisingKB:
    """A KB whose ``search`` always raises — exercises the node-local path (Req 5.5)."""

    def search(self, query: str, k: int = 10):
        raise RuntimeError("retrieval backend unavailable")


@pytest.fixture(autouse=True)
def _fake_pdf_text(monkeypatch):
    """Fake the PDF read so the Extractor runs on deterministic text (no real PDF)."""
    monkeypatch.setattr(
        extractor_mod, "read_pdf_text", lambda _source: _BILL_TEXT
    )


def _fresh_state() -> PipelineState:
    return PipelineState(session_id="s-test", language=SupportedLanguage.EN)


def test_rights_checker_appends_rights_findings_and_sets_status():
    """A matching KB -> rights_issue Finding carried to the Writer (Req 5.1)."""
    provider = _StubProvider()
    kb = _MatchingKB()
    state = _fresh_state()

    run_pipeline(state, provider, pdf_source=b"ignored", kb=kb)

    # Pipeline completed without a fatal error and produced a result.
    assert state.error is None
    assert state.result is not None

    # Rights check ran against the injected KB and completed.
    assert kb.queries, "Rights_Checker never queried the injected KB"
    assert state.rights_status == RightsStatus.COMPLETED

    # findings carry BOTH auditor findings and the rights-issue Finding.
    types = {f.type for f in state.findings}
    assert FindingType.RIGHTS_ISSUE in types, types
    assert FindingType.DUPLICATE_CHARGE in types, types

    # The Writer consumes retained_findings; it was refreshed to the FULL set,
    # so the rights Finding reaches the Writer (one summary entry per finding).
    assert any(
        f.type == FindingType.RIGHTS_ISSUE for f in state.retained_findings
    )
    assert len(state.result.summary_entries) == len(state.retained_findings)

    # Five-node progress advanced: Rights_Checker and all others COMPLETED.
    assert state.node_status["rights_checker"] == NodeStatus.COMPLETED
    assert state.node_status["writer"] == NodeStatus.COMPLETED


def test_rights_checker_failure_is_node_local_and_does_not_halt():
    """A KB whose search raises -> UNAVAILABLE, no halt, Writer still runs (Req 5.5)."""
    provider = _StubProvider()
    kb = _RaisingKB()
    state = _fresh_state()

    run_pipeline(state, provider, pdf_source=b"ignored", kb=kb)

    # Node-local: the failure did NOT set a node-fatal error or stop the run.
    assert state.error is None
    assert state.rights_status == RightsStatus.UNAVAILABLE

    # The Rights_Checker still reports COMPLETED (it completes even when the
    # rights check is unavailable) and the Writer still ran to produce a result.
    assert state.node_status["rights_checker"] == NodeStatus.COMPLETED
    assert state.node_status["writer"] == NodeStatus.COMPLETED
    assert state.result is not None

    # Auditor findings are preserved; no fabricated rights findings were added.
    types = {f.type for f in state.findings}
    assert FindingType.DUPLICATE_CHARGE in types, types
    assert FindingType.RIGHTS_ISSUE not in types, types
    assert "writer" in provider.calls
