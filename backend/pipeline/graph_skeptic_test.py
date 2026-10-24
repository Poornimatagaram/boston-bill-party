"""Focused tests for Skeptic wiring in the pipeline (task 13.2, Req 6.1, 7.1).

These exercise ``run_pipeline`` end to end (Extractor -> Auditor ->
Rights_Checker -> Skeptic -> Writer) with a deterministic stubbed LLM provider
and an INJECTED knowledge base, so no network, no real LLM, and no real corpus
are involved. They pin the Skeptic's role in the assembled pipeline:

* The Skeptic is the SOLE authority on ``state.retained_findings``: after a run,
  ``retained_findings`` equals ``apply_skeptic(state.findings)`` exactly, and is
  a STRICT SUBSET of ``findings`` for the fixture bill (the Auditor's bill-total
  mismatch math_error carries no line-item/passage refs and is dropped by the
  Skeptic per Req 6.3, while the duplicate-charge and rights-issue Findings are
  retained because they are confident and evidenced).
* The Writer produces exactly one summary entry per retained Finding (Req 7.1).
* The Skeptic node genuinely transitions to COMPLETED (five-node progress).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

import pipeline.extractor as extractor_mod
from models import FindingType, SupportedLanguage
from pipeline.graph import run_pipeline
from pipeline.skeptic import apply_skeptic, is_retained
from pipeline.state import NodeStatus, PipelineState

# Synthetic bill text + matching Extractor JSON. Two identical code+description
# lines drive a duplicate-charge Finding (evidenced -> retained); the stated
# total (250.00) differs from the sum of amounts (200.00) so the Auditor also
# emits a bill-total-mismatch math_error that carries NO line-item/passage refs
# (so the Skeptic drops it).
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
    """A KB returning one strongly-matching passage (clears the 0.7 threshold)."""

    def search(self, query: str, k: int = 10) -> list[_StubResult]:
        return [
            _StubResult(
                id="ma-duplicate-charges",
                text="Duplicate line items that repeat the same billing code "
                "and description may indicate a billing error.",
                source="MA Patient Billing Rights",
                score=100.0,
            )
        ]


@pytest.fixture(autouse=True)
def _fake_pdf_text(monkeypatch):
    """Fake the PDF read so the Extractor runs on deterministic text."""
    monkeypatch.setattr(
        extractor_mod, "read_pdf_text", lambda _source: _BILL_TEXT
    )


def _fresh_state() -> PipelineState:
    return PipelineState(session_id="s-skeptic", language=SupportedLanguage.EN)


def test_skeptic_owns_retained_findings_as_filtered_subset():
    """retained_findings == apply_skeptic(findings), a strict subset here (Req 6)."""
    provider = _StubProvider()
    kb = _MatchingKB()
    state = _fresh_state()

    run_pipeline(state, provider, pdf_source=b"ignored", kb=kb)

    assert state.error is None
    assert state.result is not None

    # The full set carries the ref-less bill-total math_error AND the evidenced
    # duplicate-charge + rights-issue Findings.
    full_types = {f.type for f in state.findings}
    assert FindingType.MATH_ERROR in full_types, full_types
    assert FindingType.DUPLICATE_CHARGE in full_types, full_types
    assert FindingType.RIGHTS_ISSUE in full_types, full_types

    # The Skeptic is the sole authority on retained_findings: it equals exactly
    # apply_skeptic(findings), preserving order.
    expected = apply_skeptic(state.findings)
    assert state.retained_findings == expected

    # And here it is a STRICT subset — the ref-less math_error was dropped.
    assert len(state.retained_findings) < len(state.findings)
    retained_types = {f.type for f in state.retained_findings}
    assert FindingType.MATH_ERROR not in retained_types, retained_types
    assert retained_types == {
        FindingType.DUPLICATE_CHARGE,
        FindingType.RIGHTS_ISSUE,
    }

    # Every retained Finding satisfies the Skeptic rule; none dropped slipped in.
    assert all(is_retained(f) for f in state.retained_findings)
    dropped = [f for f in state.findings if f not in state.retained_findings]
    assert dropped and all(not is_retained(f) for f in dropped)


def test_writer_consumes_filtered_set_one_entry_per_retained_finding():
    """Writer emits one summary entry per Skeptic-retained Finding (Req 7.1)."""
    provider = _StubProvider()
    kb = _MatchingKB()
    state = _fresh_state()

    run_pipeline(state, provider, pdf_source=b"ignored", kb=kb)

    assert state.result is not None
    # Writer saw the FILTERED set (not the full findings), one entry each.
    assert len(state.result.summary_entries) == len(state.retained_findings)
    assert len(state.result.summary_entries) < len(state.findings)


def test_skeptic_node_reports_completed():
    """The Skeptic node genuinely transitions RUNNING -> COMPLETED (five-node)."""
    provider = _StubProvider()
    kb = _MatchingKB()
    state = _fresh_state()

    run_pipeline(state, provider, pdf_source=b"ignored", kb=kb)

    assert state.node_status["skeptic"] == NodeStatus.COMPLETED
    assert state.node_status["writer"] == NodeStatus.COMPLETED
