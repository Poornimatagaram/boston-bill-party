"""Verification tests for the evaluation harness (task 16.2, Req 13).

Three groups:

* ``classify_bill`` on a hand-made case exercising a detected match, a missed
  planted error, and a false-positive Finding (Req 13.4, 13.5).
* ``run_evaluation`` over the REAL ten-bill Eval_Set with the deterministic eval
  provider (no real LLM): ten per-bill entries, zero failed bills, conserved
  totals (detected + missed == total planted), non-negative false positives, and
  a high detection rate (Req 13.1, 13.3, 13.6).
* Empty-set termination: an explicit empty list raises ``EmptyEvalSetError``
  without running the pipeline, and a failing-bill run is recorded as failed and
  continues (Req 13.2, 13.3).

No real LLM is involved: the harness injects its own deterministic
``EvalLLMProvider`` for every pipeline run.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from eval.harness import (
    EmptyEvalSetError,
    EvalLLMProvider,
    classify_bill,
    finding_matches_planted,
    parse_eval_bill_text,
    run_evaluation,
)
from eval.models import EvalBill, PlantedError
from models import Finding, FindingType


def _finding(
    finding_id: str,
    ftype: FindingType,
    line_item_ids: list[str] | None = None,
    passage_ids: list[str] | None = None,
    confidence: str = "0.9",
) -> Finding:
    return Finding(
        id=finding_id,
        type=ftype,
        description="test finding",
        confidence=Decimal(confidence),
        line_item_ids=line_item_ids or [],
        passage_ids=passage_ids or [],
    )


# ---------------------------------------------------------------------------
# classify_bill — hand-made case (Req 13.4, 13.5)
# ---------------------------------------------------------------------------


def test_classify_bill_detected_missed_and_false_positive():
    """A detected match, a missed planted error, and a false-positive Finding."""
    planted = [
        # Detected: a line-scoped suspicious_code on L2.
        PlantedError(id="p-detected", expected_type=FindingType.SUSPICIOUS_CODE, line_item_id="L2"),
        # Missed: a duplicate_charge on L5 that no retained Finding matches.
        PlantedError(id="p-missed", expected_type=FindingType.DUPLICATE_CHARGE, line_item_id="L5"),
    ]
    retained = [
        # Matches p-detected (same type + references L2).
        _finding("f-match", FindingType.SUSPICIOUS_CODE, line_item_ids=["L2"]),
        # Matches no planted error -> false positive (rights issue, not planted).
        _finding("f-fp", FindingType.RIGHTS_ISSUE, passage_ids=["kb-1"]),
    ]

    result = classify_bill(retained, planted)

    assert result.detected == 1
    assert result.missed == 1
    assert result.false_positives == 1
    assert result.detected_ids == ["p-detected"]
    assert result.missed_ids == ["p-missed"]
    assert result.false_positive_finding_ids == ["f-fp"]
    # Every planted error is exactly detected or missed.
    assert result.detected + result.missed == len(planted)


def test_match_requires_type_and_line_reference():
    """A planted error with a line id matches only a Finding referencing that line."""
    planted = PlantedError(
        id="p", expected_type=FindingType.MATH_ERROR, line_item_id="L1"
    )
    # Right type, wrong line -> no match.
    assert not finding_matches_planted(
        _finding("f", FindingType.MATH_ERROR, line_item_ids=["L9"]), planted
    )
    # Right type, right line -> match.
    assert finding_matches_planted(
        _finding("f", FindingType.MATH_ERROR, line_item_ids=["L1", "L2"]), planted
    )
    # Wrong type, right line -> no match.
    assert not finding_matches_planted(
        _finding("f", FindingType.DUPLICATE_CHARGE, line_item_ids=["L1"]), planted
    )


def test_line_agnostic_planted_error_matches_on_type_alone():
    """A planted error with no line id matches on type alone (bill-total case)."""
    planted = PlantedError(id="p", expected_type=FindingType.MATH_ERROR)
    # Type matches, no line reference needed.
    assert finding_matches_planted(
        _finding("f", FindingType.MATH_ERROR), planted
    )
    assert not finding_matches_planted(
        _finding("f", FindingType.SUSPICIOUS_CODE), planted
    )


def test_one_finding_can_satisfy_multiple_planted_errors_without_double_counting():
    """Set-based detection: one Finding can detect several planted errors."""
    # Two planted errors of the same type with no line scope.
    planted = [
        PlantedError(id="p1", expected_type=FindingType.MATH_ERROR),
        PlantedError(id="p2", expected_type=FindingType.MATH_ERROR),
    ]
    retained = [_finding("f", FindingType.MATH_ERROR)]
    result = classify_bill(retained, planted)
    # Both planted errors are detected by the single Finding; the Finding is not
    # a false positive because it matches at least one planted error.
    assert result.detected == 2
    assert result.missed == 0
    assert result.false_positives == 0


# ---------------------------------------------------------------------------
# Deterministic eval provider parsing
# ---------------------------------------------------------------------------


def test_eval_provider_parses_line_format():
    """The eval provider parses the embedded LINE/TOTAL format into bill JSON."""
    raw = (
        "PROVIDER: Test Clinic\n"
        "ACCOUNT: ACCT-X\n"
        "LINE L1 | CODE 99213 | Office visit | QTY 2 | UNIT 75.00 | AMT 150.00\n"
        "LINE L2 | CODE NONE | Fee | QTY 1 | UNIT - | AMT -\n"
        "TOTAL: 150.00\n"
    )
    payload = parse_eval_bill_text(raw)
    assert payload["provider_name"] == "Test Clinic"
    assert payload["account_number"] == "ACCT-X"
    assert payload["stated_total"] == "150.00"
    assert len(payload["line_items"]) == 2
    l1, l2 = payload["line_items"]
    assert l1 == {
        "id": "L1",
        "description": "Office visit",
        "billing_code": "99213",
        "quantity": "2",
        "unit_price": "75.00",
        "amount": "150.00",
    }
    # Absent code + absent numerics map to null.
    assert l2["billing_code"] is None
    assert l2["unit_price"] is None
    assert l2["amount"] is None


def test_eval_provider_absent_total_maps_to_none():
    payload = parse_eval_bill_text("TOTAL: NONE\n")
    assert payload["stated_total"] is None


def test_eval_provider_branches_on_node():
    provider = EvalLLMProvider()
    # Writer branch returns non-empty canned text.
    assert provider.complete(system="", user="x", timeout_s=1, node="writer").strip()
    # Extractor branch returns JSON parseable back into a bill payload.
    import json

    out = provider.complete(
        system="",
        user="LINE L1 | CODE 99213 | Visit | QTY 1 | UNIT 10.00 | AMT 10.00\nTOTAL: 10.00",
        timeout_s=1,
        node="extractor",
    )
    parsed = json.loads(out)
    assert parsed["line_items"][0]["id"] == "L1"


# ---------------------------------------------------------------------------
# run_evaluation over the real ten-bill Eval_Set (no real LLM)
# ---------------------------------------------------------------------------


def test_run_evaluation_over_real_eval_set():
    """End-to-end over the committed ten bills with the deterministic provider."""
    report = run_evaluation()

    # Req 13.1: ten bills, each run once, each with a per-bill entry.
    assert len(report.per_bill) == 10
    # The deterministic provider structures every bill, so none fails (Req 13.3).
    assert report.failed_bills == 0

    # Totals are conserved: detected + missed == total planted errors (Property 16).
    total_planted = sum(entry["planted"] for entry in report.per_bill)
    assert report.detected + report.missed == total_planted
    assert report.false_positives >= 0

    # Per-bill counts sum to the aggregate totals (Req 13.6 / Property 16).
    assert report.detected == sum(e["detected"] for e in report.per_bill)
    assert report.missed == sum(e["missed"] for e in report.per_bill)
    assert report.false_positives == sum(e["false_positives"] for e in report.per_bill)

    # The planted errors are largely detected: the bills were designed to be
    # detectable, so the detection rate is high (>= 80%).
    assert report.detected >= int(0.8 * total_planted)


def test_run_evaluation_empty_set_raises():
    """Req 13.2: an explicit empty Eval_Set terminates without running."""
    with pytest.raises(EmptyEvalSetError):
        run_evaluation(bills=[])


def test_run_evaluation_records_failed_bill_and_continues():
    """Req 13.3: a bill whose PDF cannot be read is recorded failed; others run."""
    good = EvalBill(
        id="bill_good",
        pdf_path="eval/eval_set/bill_01.pdf",
        planted_errors=[
            PlantedError(id="g1", expected_type=FindingType.MATH_ERROR, line_item_id="L1"),
        ],
    )
    bad = EvalBill(
        id="bill_bad",
        pdf_path="/nonexistent/path/does-not-exist.pdf",
        planted_errors=[
            PlantedError(id="b1", expected_type=FindingType.MATH_ERROR, line_item_id="L1"),
        ],
    )
    # Resolve the good bill's pdf_path to an absolute path (loader would do this).
    import os

    good = good.model_copy(
        update={"pdf_path": os.path.abspath(good.pdf_path)}
    )

    report = run_evaluation(bills=[good, bad])

    assert report.failed_bills == 1
    assert len(report.per_bill) == 2
    bad_entry = next(e for e in report.per_bill if e["bill_id"] == "bill_bad")
    assert bad_entry["failed"] is True
    # The failed bill's planted errors count as missed (not detected).
    assert bad_entry["missed"] == 1
    assert bad_entry["detected"] == 0
    assert bad_entry["false_positives"] == 0
    # Totals still conserve across both bills.
    total_planted = sum(e["planted"] for e in report.per_bill)
    assert report.detected + report.missed == total_planted
