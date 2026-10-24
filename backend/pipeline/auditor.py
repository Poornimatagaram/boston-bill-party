"""Deterministic Auditor for the Boston Bill Party pipeline.

The Auditor is a pure function over an :class:`ExtractedBill` and the
precomputed :class:`SumCheckResult`. It never calls the LLM and performs all
arithmetic with exact :class:`~decimal.Decimal` math, so its Findings are
reproducible across runs (design: "the auditor's per-line math comparisons use
exact decimal arithmetic").

It emits four flavors of :class:`Finding`:

Math errors (``FindingType.MATH_ERROR``):
- Bill-total mismatch: ``|computed_total - stated_total| > 0.01`` -- the Finding
  carries BOTH totals in ``computed_total``/``stated_total`` (Req 3.2).
- Absent stated total: ``stated_total is None`` -- one Finding noting the stated
  total is absent while the computed total is retained (Req 3.6).
- Unparseable line amount: every id in
  ``SumCheckResult.excluded_line_item_ids`` yields a Finding referencing that
  line (Req 3.5).
- Per-line arithmetic: ``|amount - quantity * unit_price| > 0.01`` when
  ``quantity``, ``unit_price`` and ``amount`` are all present/parseable (Req 4.3).

Duplicate charge (``FindingType.DUPLICATE_CHARGE``):
- Lines sharing an IDENTICAL ``billing_code`` AND IDENTICAL ``description`` are
  grouped; each maximal group of size >= 2 becomes exactly one Finding
  referencing ALL member line ids, in bill line order (Req 4.2). Lines with no
  billing_code do not participate in duplicate grouping (grouping requires an
  identical, present code).

Suspicious code (``FindingType.SUSPICIOUS_CODE``):
- A line whose ``billing_code`` is present but absent from the recognized set
  (Req 4.4), or missing entirely (``None``) (Req 4.5).

Every Finding carries a fixed, documented ``confidence`` in the inclusive range
[0, 1] (Req 4.6). A bill with zero line items yields an empty Findings list
(Req 4.7).

Tolerance semantics: comparisons use a strict ``> 0.01`` threshold with exact
Decimal arithmetic, so a difference of exactly ``0.01`` does NOT trigger a
Finding; only a difference strictly greater than ``0.01`` does.

Finding ids are deterministic within a run: each is derived from the finding
type plus a stable, order-preserving discriminator (the referenced line ids or
a fixed suffix), so repeated runs over the same inputs produce identical ids
and ordering.
"""

from __future__ import annotations

from decimal import Decimal

from models import (
    ExtractedBill,
    Finding,
    FindingType,
    LineItem,
    SumCheckResult,
)

# Tolerance for all monetary comparisons. A difference must be STRICTLY greater
# than this to trigger a math-error Finding; exactly this value does not
# (Req 3.2, 4.3).
_TOLERANCE = Decimal("0.01")

# Curated set of recognized billing codes. "Unrecognized" is defined relative to
# this constant so that Req 4.4 is well-defined and testable. These are a small
# handful of representative CPT/HCPCS-style codes; a code outside this set (and a
# missing code) is treated as suspicious. Comparison is case-sensitive on the
# stripped code string.
RECOGNIZED_BILLING_CODES: frozenset[str] = frozenset(
    {
        "99213",  # CPT: office/outpatient visit, established patient
        "99214",  # CPT: office/outpatient visit, established patient (moderate)
        "99283",  # CPT: emergency department visit, moderate severity
        "80053",  # CPT: comprehensive metabolic panel
        "85025",  # CPT: complete blood count (CBC) with differential
        "36415",  # CPT: routine venipuncture
        "71046",  # CPT: chest X-ray, 2 views
        "J1885",  # HCPCS: ketorolac tromethamine injection
        "Q9967",  # HCPCS: low-osmolar contrast material
        "G0463",  # HCPCS: hospital outpatient clinic visit
    }
)

# Fixed, documented confidences per finding type. All lie within [0, 1]
# (Req 4.6). Deterministic math checks are highly trustworthy; duplicate and
# suspicious-code detections are heuristic and scored a touch lower.
_CONFIDENCE_MATH_ERROR = Decimal("0.95")
_CONFIDENCE_DUPLICATE_CHARGE = Decimal("0.80")
_CONFIDENCE_SUSPICIOUS_CODE = Decimal("0.70")


def _find_line(bill: ExtractedBill, line_id: str) -> LineItem | None:
    """Return the first line item with the given id, or None."""
    for line in bill.line_items:
        if line.id == line_id:
            return line
    return None


def _math_error_bill_total_findings(
    bill: ExtractedBill, sum_check: SumCheckResult
) -> list[Finding]:
    """Bill-level math-error Findings: total mismatch or absent stated total."""
    findings: list[Finding] = []
    computed = sum_check.computed_total

    if bill.stated_total is None:
        # Stated total absent: retain the computed total, flag the absence
        # (Req 3.6).
        findings.append(
            Finding(
                id="math_error:bill_total:absent",
                type=FindingType.MATH_ERROR,
                description=(
                    "The bill has no stated total; the computed total of "
                    f"{computed} could not be reconciled against a stated "
                    "total."
                ),
                confidence=_CONFIDENCE_MATH_ERROR,
                computed_total=computed,
                stated_total=None,
            )
        )
    elif abs(computed - bill.stated_total) > _TOLERANCE:
        # Mismatch beyond tolerance: carry BOTH totals (Req 3.2).
        findings.append(
            Finding(
                id="math_error:bill_total:mismatch",
                type=FindingType.MATH_ERROR,
                description=(
                    "The sum of the line-item amounts "
                    f"({computed}) differs from the stated bill total "
                    f"({bill.stated_total}) by more than {_TOLERANCE}."
                ),
                confidence=_CONFIDENCE_MATH_ERROR,
                computed_total=computed,
                stated_total=bill.stated_total,
            )
        )

    return findings


def _math_error_unparseable_findings(
    bill: ExtractedBill, sum_check: SumCheckResult
) -> list[Finding]:
    """Math-error Findings for each line excluded by the Sum_Check (Req 3.5)."""
    findings: list[Finding] = []
    for line_id in sum_check.excluded_line_item_ids:
        line = _find_line(bill, line_id)
        label = f' "{line.description}"' if line is not None else ""
        findings.append(
            Finding(
                id=f"math_error:unparseable:{line_id}",
                type=FindingType.MATH_ERROR,
                description=(
                    f"Line item{label} has a missing or unparseable amount and "
                    "was excluded from the computed total."
                ),
                confidence=_CONFIDENCE_MATH_ERROR,
                line_item_ids=[line_id],
            )
        )
    return findings


def _math_error_per_line_findings(bill: ExtractedBill) -> list[Finding]:
    """Per-line math-error Findings: amount != quantity * unit_price (Req 4.3).

    Only evaluated when quantity, unit_price and amount are all present and
    finite; otherwise the line is skipped here (missing amounts are handled via
    the Sum_Check exclusion path).
    """
    findings: list[Finding] = []
    for line in bill.line_items:
        quantity = line.quantity
        unit_price = line.unit_price
        amount = line.amount
        if quantity is None or unit_price is None or amount is None:
            continue
        if not (quantity.is_finite() and unit_price.is_finite() and amount.is_finite()):
            continue
        expected = quantity * unit_price
        if abs(amount - expected) > _TOLERANCE:
            findings.append(
                Finding(
                    id=f"math_error:line_math:{line.id}",
                    type=FindingType.MATH_ERROR,
                    description=(
                        f'Line item "{line.description}" has an amount of '
                        f"{amount} that does not equal quantity "
                        f"({quantity}) multiplied by unit price "
                        f"({unit_price}) = {expected}."
                    ),
                    confidence=_CONFIDENCE_MATH_ERROR,
                    line_item_ids=[line.id],
                )
            )
    return findings


def _duplicate_charge_findings(bill: ExtractedBill) -> list[Finding]:
    """Duplicate-charge Findings grouping identical (code, description) (Req 4.2).

    Grouping requires a present billing_code; the key is the exact
    ``(billing_code, description)`` pair. Each maximal group of size >= 2 yields
    exactly one Finding referencing every member's id, in bill line order. Group
    order follows first appearance in the bill, keeping output deterministic.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    order: list[tuple[str, str]] = []
    for line in bill.line_items:
        if line.billing_code is None:
            # No code -> cannot be an identical-code duplicate (handled as a
            # suspicious-code Finding instead).
            continue
        key = (line.billing_code, line.description)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(line.id)

    findings: list[Finding] = []
    for key in order:
        member_ids = groups[key]
        if len(member_ids) < 2:
            continue
        billing_code, description = key
        joined = "+".join(member_ids)
        findings.append(
            Finding(
                id=f"duplicate_charge:{joined}",
                type=FindingType.DUPLICATE_CHARGE,
                description=(
                    f'{len(member_ids)} line items share billing code '
                    f'"{billing_code}" and description "{description}", '
                    "indicating a possible duplicate charge."
                ),
                confidence=_CONFIDENCE_DUPLICATE_CHARGE,
                line_item_ids=list(member_ids),
            )
        )
    return findings


def _suspicious_code_findings(bill: ExtractedBill) -> list[Finding]:
    """Suspicious-code Findings for missing or unrecognized codes (Req 4.4, 4.5)."""
    findings: list[Finding] = []
    for line in bill.line_items:
        code = line.billing_code
        if code is None:
            findings.append(
                Finding(
                    id=f"suspicious_code:missing:{line.id}",
                    type=FindingType.SUSPICIOUS_CODE,
                    description=(
                        f'Line item "{line.description}" has no billing code.'
                    ),
                    confidence=_CONFIDENCE_SUSPICIOUS_CODE,
                    line_item_ids=[line.id],
                )
            )
        elif code.strip() not in RECOGNIZED_BILLING_CODES:
            findings.append(
                Finding(
                    id=f"suspicious_code:unrecognized:{line.id}",
                    type=FindingType.SUSPICIOUS_CODE,
                    description=(
                        f'Line item "{line.description}" has billing code '
                        f'"{code}", which is not in the set of recognized '
                        "billing codes."
                    ),
                    confidence=_CONFIDENCE_SUSPICIOUS_CODE,
                    line_item_ids=[line.id],
                )
            )
    return findings


def audit(bill: ExtractedBill, sum_check: SumCheckResult) -> list[Finding]:
    """Audit an ``ExtractedBill`` against its ``SumCheckResult`` (pure).

    Produces the full set of Findings described in this module's docstring. The
    function performs no I/O, invokes no LLM, and is fully deterministic: the
    same ``(bill, sum_check)`` inputs always yield identical Findings, ids, and
    ordering. A bill with zero line items yields an empty list (Req 4.7).

    Finding ordering is stable and grouped by category: bill-level math errors,
    per-line math errors (unparseable then arithmetic), duplicate charges, then
    suspicious codes.

    Args:
        bill: The structured bill to audit.
        sum_check: The precomputed deterministic Sum_Check for ``bill``.

    Returns:
        A deterministic list of :class:`Finding` objects (possibly empty).
    """
    if not bill.line_items:
        # Zero line items -> empty Findings (Req 4.7). Even a bill-total check is
        # moot without charges to reconcile.
        return []

    findings: list[Finding] = []
    findings.extend(_math_error_bill_total_findings(bill, sum_check))
    findings.extend(_math_error_unparseable_findings(bill, sum_check))
    findings.extend(_math_error_per_line_findings(bill))
    findings.extend(_duplicate_charge_findings(bill))
    findings.extend(_suspicious_code_findings(bill))
    return findings


def audit_bill(bill: ExtractedBill) -> list[Finding]:
    """Convenience wrapper: run the Sum_Check then audit the bill.

    This mirrors how the Auditor node operates in the pipeline (the Sum_Check is
    invoked at the start of the Auditor). The pure :func:`audit` function remains
    the primary, import-friendly entry point for testing.

    Args:
        bill: The structured bill to audit.

    Returns:
        The list of :class:`Finding` objects produced by :func:`audit`.
    """
    from pipeline.sumcheck import compute_sum_check

    return audit(bill, compute_sum_check(bill))
