"""Property-based test for Auditor duplicate-charge grouping (pipeline/auditor.py).

Uses Hypothesis to generate random ExtractedBills with codes/descriptions drawn
from a small pool so collisions (duplicates) occur frequently, then verifies the
duplicate_charge Findings exactly partition into the maximal groups of lines
sharing an identical, present billing_code AND identical description.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from models import ExtractedBill, FindingType, LineItem
from pipeline.auditor import audit
from pipeline.sumcheck import compute_sum_check

# Small pools so that (code, description) collisions happen often. Including
# None in the code pool exercises lines that cannot participate in grouping.
_codes = st.sampled_from([None, "99213", "80053", "XX000", "99214"])
_descriptions = st.sampled_from(["Office visit", "Lab panel", "X-ray"])
_amounts = st.one_of(
    st.none(),
    st.decimals(
        min_value=Decimal("0.00"),
        max_value=Decimal("5000.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)


@st.composite
def extracted_bills(draw):
    """Generate ExtractedBills with varying line counts and codes/descriptions
    drawn from a small pool so duplicates arise frequently. Each LineItem gets a
    unique id so finding references are checkable."""
    n = draw(st.integers(min_value=0, max_value=10))
    line_items = []
    for i in range(n):
        line_items.append(
            LineItem(
                id=f"li-{i}",  # unique within the bill
                description=draw(_descriptions),
                billing_code=draw(_codes),
                amount=draw(_amounts),
            )
        )
    return ExtractedBill(line_items=line_items)


def _expected_groups(bill: ExtractedBill) -> list[list[str]]:
    """Independently compute the expected duplicate groups: lines grouped by
    (billing_code, description) excluding None codes, keeping groups of size >= 2,
    with member ids in bill line order and groups in first-appearance order."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    order: list[tuple[str, str]] = []
    for line in bill.line_items:
        if line.billing_code is None:
            continue
        key = (line.billing_code, line.description)
        if key not in groups:
            order.append(key)
        groups[key].append(line.id)
    return [groups[key] for key in order if len(groups[key]) >= 2]


# Feature: boston-bill-party, Property 6: Duplicate charges are grouped into one Finding referencing all members
# Validates: Requirements 4.2
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(bill=extracted_bills())
def test_duplicate_charges_grouped_into_one_finding(bill: ExtractedBill) -> None:
    sum_check = compute_sum_check(bill)
    findings = audit(bill, sum_check)

    dup_findings = [f for f in findings if f.type == FindingType.DUPLICATE_CHARGE]
    expected_groups = _expected_groups(bill)

    # Exactly one duplicate_charge Finding per qualifying group.
    assert len(dup_findings) == len(expected_groups)

    # Each expected group matches a duplicate Finding by set of member ids, and
    # the referenced ids are in bill line order.
    dup_by_members = {frozenset(f.line_item_ids): f.line_item_ids for f in dup_findings}
    assert len(dup_by_members) == len(dup_findings)  # no two findings share members

    for group in expected_groups:
        key = frozenset(group)
        assert key in dup_by_members, f"missing duplicate finding for group {group}"
        # Member ids are exactly the group, in bill order.
        assert dup_by_members[key] == group

    # The union of all duplicate findings' ids partitions exactly into the
    # qualifying groups -> lines not in any group appear in no duplicate finding.
    all_dup_ids: list[str] = []
    for f in dup_findings:
        all_dup_ids.extend(f.line_item_ids)
    expected_ids = [lid for group in expected_groups for lid in group]
    assert sorted(all_dup_ids) == sorted(expected_ids)
    assert len(all_dup_ids) == len(set(all_dup_ids))  # no id referenced twice
