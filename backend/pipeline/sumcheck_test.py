"""Property-based tests for the deterministic Sum_Check (pipeline/sumcheck.py).

Uses Hypothesis to generate random ExtractedBills and checks that the computed
Sum_Check total matches an independently-computed exact Decimal sum of the
present/parseable Line_Item amounts.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from models import ExtractedBill, LineItem
from pipeline.sumcheck import compute_sum_check

# Quantizer matching SumCheckResult's two-decimal-place handling.
_TWO_PLACES = Decimal("0.01")

# Non-negative, 2dp-friendly amounts within LineItem's ge=0 constraint.
# Hypothesis' decimals strategy with places=2 keeps values to two decimal
# places; min_value=0 honors the ge=0 constraint.
_amounts = st.one_of(
    st.none(),  # missing amount -> excluded + flagged (Req 3.5)
    st.decimals(
        min_value=Decimal("0.00"),
        max_value=Decimal("100000.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)


@st.composite
def extracted_bills(draw):
    """Generate ExtractedBills with varying line counts (including zero),
    present and missing amounts, and a unique id per LineItem so that
    excluded-id tracking is checkable."""
    n = draw(st.integers(min_value=0, max_value=12))
    line_items = []
    for i in range(n):
        amount = draw(_amounts)
        line_items.append(
            LineItem(
                id=f"li-{i}",  # unique within the bill
                description=f"item {i}",
                amount=amount,
            )
        )
    return ExtractedBill(line_items=line_items)


# Feature: boston-bill-party, Property 1: Sum_Check equals the exact sum of parseable amounts
# Validates: Requirements 3.1, 3.5
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(bill=extracted_bills())
def test_sum_check_equals_exact_sum_of_parseable_amounts(bill: ExtractedBill) -> None:
    result = compute_sum_check(bill)

    # Independently compute the exact decimal sum of present, parseable amounts.
    expected = Decimal("0.00")
    expected_excluded: list[str] = []
    for li in bill.line_items:
        if li.amount is None or not li.amount.is_finite():
            expected_excluded.append(li.id)
        else:
            expected += li.amount

    # Compare by Decimal value, accounting for 2dp handling on both sides.
    assert result.computed_total.quantize(_TWO_PLACES) == expected.quantize(
        _TWO_PLACES
    )

    # When there are no present/parseable amounts (including the empty bill),
    # the computed total is 0.00 (Req 3.1).
    if not any(
        li.amount is not None and li.amount.is_finite() for li in bill.line_items
    ):
        assert result.computed_total.quantize(_TWO_PLACES) == Decimal("0.00")

    # Excluded ids are exactly the missing/unparseable ones, in bill line order (Req 3.5).
    assert result.excluded_line_item_ids == expected_excluded
