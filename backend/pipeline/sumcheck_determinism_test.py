"""Property-based test for Sum_Check determinism (Property 2).

# Feature: boston-bill-party, Property 2: Sum_Check is deterministic

For any ExtractedBill, computing the Sum_Check two or more times yields an
identical result on every execution (Req 3.4). This test exercises the pure
``compute_sum_check`` function with randomly generated bills and asserts that
repeated calls on the same bill produce value-equal and order-equal
``computed_total`` and ``excluded_line_item_ids``.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from models import ExtractedBill, LineItem
from pipeline.sumcheck import compute_sum_check

# Non-negative two-decimal Decimal amounts, matching LineItem.amount (ge=0),
# plus None to cover the missing/unparseable-amount path (Req 3.5).
_amounts = st.one_of(
    st.none(),
    st.decimals(
        min_value=Decimal("0.00"),
        max_value=Decimal("100000.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)


@st.composite
def _extracted_bills(draw: st.DrawFn) -> ExtractedBill:
    """Generate random ExtractedBills with unique line ids.

    Varies the line count (including zero), uses non-negative Decimal amounts
    and some ``None`` amounts, and guarantees unique line-item ids so the
    excluded-id ordering is unambiguous.
    """
    count = draw(st.integers(min_value=0, max_value=12))
    line_items = []
    for index in range(count):
        amount = draw(_amounts)
        line_items.append(
            LineItem(
                id=f"line-{index}",  # unique within the bill
                description=draw(st.text(min_size=0, max_size=20)),
                amount=amount,
            )
        )
    stated_total = draw(_amounts)
    return ExtractedBill(line_items=line_items, stated_total=stated_total)


@settings(max_examples=200)
@given(bill=_extracted_bills())
def test_sum_check_is_deterministic(bill: ExtractedBill) -> None:
    """Repeated Sum_Check calls on the same bill are identical (Property 2)."""
    first = compute_sum_check(bill)
    second = compute_sum_check(bill)
    third = compute_sum_check(bill)

    # Computed total is identical in value across every execution.
    assert first.computed_total == second.computed_total == third.computed_total

    # Excluded ids are identical in both value and order across executions.
    assert first.excluded_line_item_ids == second.excluded_line_item_ids
    assert first.excluded_line_item_ids == third.excluded_line_item_ids
