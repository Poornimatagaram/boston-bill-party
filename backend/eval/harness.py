"""Evaluation harness over the synthetic Eval_Set (Req 13).

This module runs the REAL pipeline once per synthetic bill and scores the
retained Findings against each bill's planted errors, producing an aggregate
:class:`~eval.models.EvalReport`.

Why it is hermetic (no real LLM, no network, no API credits)
------------------------------------------------------------
The pipeline's only LLM-dependent steps are the Extractor's *structuring* of raw
PDF text into an :class:`~models.ExtractedBill` and the Writer's prose
generation. Every other node (Sum_Check, Auditor, Rights_Checker, Skeptic) is
deterministic and performs no LLM I/O. To evaluate detection quality
deterministically we inject a purpose-built :class:`EvalLLMProvider` into
``run_pipeline`` that branches on the ``node`` argument:

* ``node == "extractor"`` — the ``user`` text it receives is the raw PDF text
  that the Extractor already read with pdfplumber. The provider PARSES that text
  (the fixed ``LINE ... | CODE ... | ...`` / ``TOTAL: ...`` format the eval PDFs
  embed; see ``eval/eval_set/bills_data.py``) into the exact JSON the Extractor's
  ``structure_bill`` validates into an ``ExtractedBill``. No model is called.
* ``node == "writer"`` — returns a short canned, non-empty string so the Writer
  node completes (it calls the provider once per retained Finding for the summary
  and once for the appeal letter). The prose content is irrelevant to detection
  scoring; only that the Writer completes matters.
* any other node — returns an empty string (never reached: only Extractor and
  Writer call the provider).

Because the Extractor still reads the PDF with the real pdfplumber path and only
the structuring step is stubbed, the Sum_Check, Auditor, Rights_Checker (real
BM25 KB), and Skeptic all run for real — exactly the nodes whose detection
quality we are measuring. The provider makes NO network calls and is fully
deterministic, so ``POST /eval/run`` consumes no API credits and yields the same
report every time.

Matching rule (Req 13.4 / 13.5) — the pure :func:`classify_bill`
----------------------------------------------------------------
Matching a retained ``Finding`` against a planted error uses this rule:

    A retained Finding MATCHES a planted error iff
      (1) ``finding.type == planted_error.expected_type``  AND
      (2) if the planted error has a ``line_item_id``, that id appears in
          ``finding.line_item_ids``; if the planted error has NO ``line_item_id``
          (e.g. a bill-total mismatch or absent-total math error, which carry no
          line references), condition (2) is vacuously satisfied — type alone
          decides.

Classification is SET-BASED (design Property 15), not a one-to-one assignment:

* a planted error is DETECTED iff AT LEAST ONE retained Finding matches it, else
  MISSED (Req 13.4);
* a retained Finding is a FALSE POSITIVE iff it matches NO planted error
  (Req 13.5).

So one Finding may satisfy several planted errors and several Findings may each
satisfy the same planted error; neither double-counts, because detection is a
pure existence test per planted error and false-positivity is a pure "matches
nothing" test per Finding. This is the exact rule the Property 15 test (task
16.3) targets, and it lives in the importable pure :func:`classify_bill` so that
test can drive it directly.

Failed bills (Req 13.3)
-----------------------
If a pipeline run raises, or finishes with ``state.error`` set, or produces no
result, the bill is recorded as FAILED and the harness continues with the
remaining bills; the failed count is carried in the report. A failed bill
contributes NO false positives (there are no trustworthy retained Findings) and
its planted errors are counted as MISSED — they were, in fact, not detected
(the run did not surface them). This keeps ``detected + missed`` equal to the
total planted-error count across every bill, failed or not, so the totals are
conserved (design Property 16).

Expected detection rate over the real ten-bill set
---------------------------------------------------
Running ``run_evaluation()`` over the committed ten-bill set detects 24 of the
26 planted errors (0 failed bills). The two "missed" planted errors are both
bill-level math errors that carry NO line-item reference — bill_02's bill-total
mismatch and bill_06's absent stated total. These are genuinely produced by the
Auditor, but the Skeptic then drops them because they reference zero line items
AND zero passages (Req 6.3), so they never reach ``retained_findings``. The
harness scores the Skeptic-retained set (Req 13.1/13.4), so those two are
correctly counted as missed. This is the pipeline's own documented behavior, not
a harness artifact. The false positives are the real BM25 Rights_Checker's
``rights_issue`` Findings, which are intentionally NOT planted (only
``math_error``/``duplicate_charge``/``suspicious_code`` are planted), so every
retained rights Finding matches no planted error and is a false positive by the
rule below.

Empty Eval_Set (Req 13.2)
-------------------------
``run_evaluation`` guards against a zero-bill set and raises
:class:`EmptyEvalSetError` WITHOUT running the pipeline. ``load_eval_set`` already
enforces exactly ten bills, but an explicit empty list passed to
``run_evaluation`` is still guarded here so the endpoint can map it to a clear
4xx.

Security note: this module never reads, logs, or prints the LLM API key. The
injected :class:`EvalLLMProvider` holds no credentials and makes no network
calls.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from eval.eval_set.loader import load_eval_set
from eval.models import EvalBill, EvalReport, PlantedError
from models import Finding, SupportedLanguage
from pipeline.graph import run_pipeline
from pipeline.state import PipelineState

# The eval provider's canned Writer output. Must be non-empty so the Writer node
# treats it as a successful generation (see pipeline/writer.py ``_require_text``).
_WRITER_CANNED_TEXT = (
    "This is deterministic evaluation placeholder prose. It exists only so the "
    "Writer node completes during evaluation; its content is not scored."
)

# Tokens used in the eval PDFs' machine-parseable line format (mirrors
# eval/eval_set/bills_data.py). ``NONE`` marks an absent billing code; ``-``
# marks an absent numeric field; ``TOTAL: NONE`` marks no stated total.
_CODE_NONE = "NONE"
_NUM_NONE = "-"


class EmptyEvalSetError(Exception):
    """Raised when evaluation is requested over a zero-bill Eval_Set (Req 13.2)."""


# ---------------------------------------------------------------------------
# Deterministic eval provider
# ---------------------------------------------------------------------------


def _parse_line_item(row: str) -> Optional[dict]:
    """Parse one ``LINE ...`` row into an ExtractedBill line-item dict.

    Expected shape (fields separated by " | "):
        ``LINE <id> | CODE <code|NONE> | <description> | QTY <q|-> | UNIT <u|-> | AMT <a|->``

    Returns ``None`` for a row that does not start with ``LINE`` so non-item
    lines (headers, banner) are ignored. A missing code (``NONE``) or numeric
    field (``-``) maps to ``null`` so the downstream schema keeps the field
    optional (Req 2.4). Amounts/quantities are left as strings; the Extractor's
    normalization coerces them to ``Decimal`` (Req 2.5).
    """
    stripped = row.strip()
    if not stripped.startswith("LINE "):
        return None
    parts = [segment.strip() for segment in stripped.split(" | ")]
    # Defensive: a malformed row that does not split into the six segments is
    # skipped rather than producing a half-built item.
    if len(parts) != 6:
        return None

    line_token, code_token, description, qty_token, unit_token, amt_token = parts

    line_id = line_token[len("LINE ") :].strip()
    code = code_token[len("CODE ") :].strip()
    quantity = qty_token[len("QTY ") :].strip()
    unit = unit_token[len("UNIT ") :].strip()
    amount = amt_token[len("AMT ") :].strip()

    return {
        "id": line_id,
        "description": description,
        "billing_code": None if code == _CODE_NONE else code,
        "quantity": None if quantity == _NUM_NONE else quantity,
        "unit_price": None if unit == _NUM_NONE else unit,
        "amount": None if amount == _NUM_NONE else amount,
    }


def _parse_header(lines: list[str]) -> dict:
    """Extract the optional provider/account header fields from the text."""
    header: dict = {
        "provider_name": None,
        "provider_address": None,
        "account_number": None,
    }
    for line in lines:
        text = line.strip()
        if text.startswith("PROVIDER:"):
            header["provider_name"] = text[len("PROVIDER:") :].strip() or None
        elif text.startswith("ADDRESS:"):
            header["provider_address"] = text[len("ADDRESS:") :].strip() or None
        elif text.startswith("ACCOUNT:"):
            header["account_number"] = text[len("ACCOUNT:") :].strip() or None
    return header


def parse_eval_bill_text(raw_text: str) -> dict:
    """Parse the eval PDF text into an ``ExtractedBill``-shaped JSON dict.

    This is the deterministic structuring that the eval provider performs in
    place of a real LLM. It reads the fixed line format the eval PDFs embed and
    returns a dict matching the ``ExtractedBill`` schema (``provider_name``,
    ``provider_address``, ``account_number``, ``line_items``, ``stated_total``).
    The returned dict is what the Extractor's ``structure_bill`` normalizes and
    validates, so the rest of the pipeline sees a genuine ``ExtractedBill``.
    """
    lines = raw_text.splitlines()

    payload: dict = _parse_header(lines)

    line_items: list[dict] = []
    for line in lines:
        item = _parse_line_item(line)
        if item is not None:
            line_items.append(item)
    payload["line_items"] = line_items

    stated_total: Optional[str] = None
    for line in lines:
        text = line.strip()
        if text.startswith("TOTAL:"):
            value = text[len("TOTAL:") :].strip()
            stated_total = None if value == _CODE_NONE else value
            break
    payload["stated_total"] = stated_total

    return payload


@dataclass
class EvalLLMProvider:
    """Deterministic, hermetic ``LLMProvider`` for evaluation (no network).

    Branches on the ``node`` argument of :meth:`complete`:

    * extractor -> parse the raw PDF ``user`` text into ExtractedBill JSON;
    * writer    -> return a short canned, non-empty string so the Writer node
      completes;
    * anything else -> empty string (never reached in practice).

    Holds no credentials and performs no I/O beyond parsing the text it is
    handed. Fully deterministic: identical inputs yield identical outputs.
    """

    def complete(
        self, *, system: str, user: str, timeout_s: float, node: Any
    ) -> str:
        import json as _json

        node_value = getattr(node, "value", node)
        if node_value == "extractor":
            return _json.dumps(parse_eval_bill_text(user))
        if node_value == "writer":
            return _WRITER_CANNED_TEXT
        return ""


# ---------------------------------------------------------------------------
# Pure matching / classification (Req 13.4, 13.5 — design Property 15)
# ---------------------------------------------------------------------------


def finding_matches_planted(finding: Finding, planted: PlantedError) -> bool:
    """Return whether a retained ``Finding`` matches a planted error.

    The rule (documented in the module docstring):

    1. the Finding's ``type`` equals the planted error's ``expected_type``; AND
    2. if the planted error names a ``line_item_id``, that id is among the
       Finding's ``line_item_ids``; if the planted error names no line
       (``line_item_id is None``), type alone decides (condition 2 is vacuous).

    Pure and total: no I/O, no mutation.
    """
    if finding.type != planted.expected_type:
        return False
    if planted.line_item_id is None:
        return True
    return planted.line_item_id in finding.line_item_ids


@dataclass
class BillClassification:
    """Per-bill classification result (Req 13.4, 13.5).

    ``detected`` + ``missed`` always equals ``len(planted_errors)`` for the bill
    (every planted error is exactly one of detected/missed). ``detected_ids`` /
    ``missed_ids`` name the planted errors; ``false_positive_finding_ids`` names
    the retained Findings matching no planted error.
    """

    detected: int = 0
    missed: int = 0
    false_positives: int = 0
    detected_ids: list[str] = field(default_factory=list)
    missed_ids: list[str] = field(default_factory=list)
    false_positive_finding_ids: list[str] = field(default_factory=list)


def classify_bill(
    retained_findings: list[Finding],
    planted_errors: list[PlantedError],
) -> BillClassification:
    """Classify one bill's retained Findings against its planted errors (pure).

    Set-based classification (design Property 15):

    * each planted error is DETECTED iff at least one retained Finding matches it
      (via :func:`finding_matches_planted`), else MISSED (Req 13.4);
    * each retained Finding is a FALSE POSITIVE iff it matches no planted error
      (Req 13.5).

    This performs no I/O and does not mutate its inputs, so the Property 15 test
    (task 16.3) can drive it directly with generated Finding/planted-error sets.

    Args:
        retained_findings: the Skeptic-retained Findings for the bill.
        planted_errors: the planted errors recorded for the bill.

    Returns:
        A :class:`BillClassification` with per-bill detected/missed/false-positive
        counts and the ids behind each count.
    """
    classification = BillClassification()

    for planted in planted_errors:
        if any(finding_matches_planted(f, planted) for f in retained_findings):
            classification.detected += 1
            classification.detected_ids.append(planted.id)
        else:
            classification.missed += 1
            classification.missed_ids.append(planted.id)

    for finding in retained_findings:
        if not any(finding_matches_planted(finding, p) for p in planted_errors):
            classification.false_positives += 1
            classification.false_positive_finding_ids.append(finding.id)

    return classification


# ---------------------------------------------------------------------------
# Running one bill through the real pipeline
# ---------------------------------------------------------------------------


@dataclass
class BillRun:
    """Outcome of running one bill through the pipeline.

    ``failed`` is True when the pipeline raised, finished with ``state.error``,
    or produced no result (Req 13.3). ``retained_findings`` is the post-Skeptic
    set on success (empty for a failed run). ``error_message`` carries a short
    reason for a failed run (never includes credentials).
    """

    retained_findings: list[Finding] = field(default_factory=list)
    failed: bool = False
    error_message: Optional[str] = None


def _run_one_bill(
    bill: EvalBill,
    provider: EvalLLMProvider,
    kb: Any = None,
) -> BillRun:
    """Run the REAL pipeline once for ``bill`` with the deterministic provider.

    Reads the bill's ``pdf_path`` with the real Extractor/pdfplumber path and
    runs Sum_Check -> Auditor -> Rights_Checker -> Skeptic -> Writer. The
    returned :class:`BillRun` reports either the retained Findings (success) or a
    failure flag with a short message (Req 13.3). Any exception raised by the
    pipeline is caught and converted into a failed run so one bad bill never
    aborts the whole evaluation.
    """
    state = PipelineState(
        session_id=f"eval-{bill.id}",
        language=SupportedLanguage.EN,
    )

    # Read the PDF bytes ourselves and hand ``run_pipeline`` a fresh stream so a
    # per-bill read never depends on a mutable open file handle.
    try:
        with open(bill.pdf_path, "rb") as fh:
            pdf_bytes = fh.read()
    except OSError as exc:
        return BillRun(failed=True, error_message=f"could not read PDF: {exc}")

    try:
        result_state = run_pipeline(
            state,
            provider,
            io.BytesIO(pdf_bytes),
            progress=None,
            kb=kb,
        )
    except Exception as exc:  # pipeline failure -> record failed, continue (Req 13.3)
        return BillRun(failed=True, error_message=str(exc))

    # A node-fatal error, or no Writer result, counts as a failed bill (Req 13.3).
    if result_state.error is not None:
        message = "pipeline error"
        if isinstance(result_state.error, dict):
            message = str(result_state.error.get("message", message))
        return BillRun(failed=True, error_message=message)
    if result_state.result is None:
        return BillRun(failed=True, error_message="pipeline produced no result")

    return BillRun(retained_findings=list(result_state.retained_findings))


# ---------------------------------------------------------------------------
# Top-level evaluation (Req 13.1, 13.2, 13.3, 13.6)
# ---------------------------------------------------------------------------


def run_evaluation(
    bills: Optional[list[EvalBill]] = None,
    *,
    provider: Optional[EvalLLMProvider] = None,
    kb: Any = None,
) -> EvalReport:
    """Run the evaluation over the Eval_Set and return the aggregate report.

    For each bill, runs the REAL pipeline once with the deterministic
    :class:`EvalLLMProvider` (so no real LLM is called) and records the retained
    Findings (Req 13.1). A failed run is recorded and evaluation continues with
    the remaining bills, with the failed count carried in the report (Req 13.3).
    Each bill's retained Findings are classified against its planted errors via
    the pure :func:`classify_bill` (Req 13.4, 13.5), and the per-bill counts are
    summed into the report totals (Req 13.6).

    Args:
        bills: the bills to evaluate. Defaults to :func:`load_eval_set` (the real
            ten-bill set). An explicit empty list raises
            :class:`EmptyEvalSetError` (Req 13.2).
        provider: optional deterministic provider override (tests). Defaults to a
            fresh :class:`EvalLLMProvider`.
        kb: optional knowledge base for the Rights_Checker. ``None`` lets
            ``run_pipeline`` use the shared real BM25 KB (deterministic).

    Returns:
        An :class:`EvalReport` with aggregate detected/missed/false-positive
        counts, the failed-bill count, and a per-bill breakdown.

    Raises:
        EmptyEvalSetError: when ``bills`` is an empty list (Req 13.2). The
            pipeline is NOT run in this case.
    """
    if bills is None:
        bills = load_eval_set()

    # Req 13.2: a zero-bill Eval_Set terminates without running the pipeline.
    if len(bills) == 0:
        raise EmptyEvalSetError(
            "The Eval_Set is empty; evaluation cannot run without any bills."
        )

    eval_provider = provider if provider is not None else EvalLLMProvider()

    total_detected = 0
    total_missed = 0
    total_false_positives = 0
    failed_bills = 0
    per_bill: list[dict] = []

    for bill in bills:
        run = _run_one_bill(bill, eval_provider, kb=kb)

        if run.failed:
            # Failed bill (Req 13.3): no trustworthy Findings. Its planted errors
            # are all MISSED (not detected), and it contributes no false
            # positives. This keeps detected + missed == total planted errors.
            failed_bills += 1
            missed = len(bill.planted_errors)
            total_missed += missed
            per_bill.append(
                {
                    "bill_id": bill.id,
                    "failed": True,
                    "error": run.error_message,
                    "planted": len(bill.planted_errors),
                    "detected": 0,
                    "missed": missed,
                    "false_positives": 0,
                    "detected_ids": [],
                    "missed_ids": [e.id for e in bill.planted_errors],
                    "false_positive_finding_ids": [],
                }
            )
            continue

        classification = classify_bill(run.retained_findings, bill.planted_errors)
        total_detected += classification.detected
        total_missed += classification.missed
        total_false_positives += classification.false_positives
        per_bill.append(
            {
                "bill_id": bill.id,
                "failed": False,
                "planted": len(bill.planted_errors),
                "detected": classification.detected,
                "missed": classification.missed,
                "false_positives": classification.false_positives,
                "detected_ids": classification.detected_ids,
                "missed_ids": classification.missed_ids,
                "false_positive_finding_ids": classification.false_positive_finding_ids,
            }
        )

    return EvalReport(
        detected=total_detected,
        missed=total_missed,
        false_positives=total_false_positives,
        failed_bills=failed_bills,
        per_bill=per_bill,
    )
