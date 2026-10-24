"""Deterministic Sum_Check for the Boston Bill Party pipeline.

The Sum_Check adds every present, parseable Line_Item amount using exact
decimal arithmetic -- never the LLM -- so arithmetic errors are caught
reliably and reproducibly (Req 3.3, 3.4).

Behavior:
- Sums all present, parseable Line_Item amounts with ``Decimal`` (Req 3.1).
- A bill with zero Line_Items, or one whose amounts are all missing/unparseable,
  yields a computed total of ``0.00`` (Req 3.1).
- Missing or unparseable amounts are excluded from the total and their
  Line_Item ids are recorded in ``excluded_line_item_ids`` -- in bill line
  order -- without aborting the computation (Req 3.5).

At this layer ``LineItem.amount`` is already a validated ``Optional[Decimal]``
(see ``models.py``), so "unparseable" primarily means the amount is ``None``.
A non-finite ``Decimal`` (NaN/Infinity) is also treated defensively as
unparseable so a single bad value can never poison the total.
"""

from __future__ import annotations

from decimal import Decimal

from models import ExtractedBill, SumCheckResult

# Computed total for a bill with no summable amounts (Req 3.1).
_ZERO = Decimal("0.00")


def compute_sum_check(bill: ExtractedBill) -> SumCheckResult:
    """Compute the deterministic Sum_Check for an ``ExtractedBill``.

    Adds every present, parseable Line_Item amount with ``Decimal`` and returns
    a :class:`SumCheckResult`. Line items whose amount is missing or unparseable
    are excluded from the total and flagged in ``excluded_line_item_ids`` in
    bill line order, without aborting (Req 3.1, 3.5). Uses exact decimal
    arithmetic only and is fully deterministic (Req 3.3, 3.4).

    Args:
        bill: The structured bill to total.

    Returns:
        A ``SumCheckResult`` with the computed total (``0.00`` when there are no
        summable amounts) and the ids of excluded line items.
    """
    computed_total = _ZERO
    excluded_line_item_ids: list[str] = []

    for line_item in bill.line_items:
        amount = line_item.amount
        if amount is None or not amount.is_finite():
            # Missing or defensively-unparseable amount: exclude and flag,
            # preserving bill line order (Req 3.5).
            excluded_line_item_ids.append(line_item.id)
            continue
        computed_total += amount

    return SumCheckResult(
        computed_total=computed_total,
        excluded_line_item_ids=excluded_line_item_ids,
    )
