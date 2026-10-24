"""Property-based tests for the Skeptic inclusion rule (pipeline/skeptic.py).

Uses Hypothesis to generate random lists of Findings with confidences drawn
around the 0.5 boundary and with varying evidence (zero/one/both of
``line_item_ids`` and ``passage_ids``), then checks that ``apply_skeptic``
retains a Finding exactly when it is confident (confidence >= 0.5) and
evidenced (at least one line-item or passage reference).
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from models import Finding, FindingType
from pipeline.skeptic import apply_skeptic, is_retained

# Confidence quantizer -- confidence is a Decimal in [0, 1] on the model.
_MIN_CONFIDENCE = Decimal("0.5")

# Confidences concentrated around the 0.5 boundary: exactly 0.5, just below,
# just above, plus a spread across the full [0, 1] range.
_confidences = st.one_of(
    st.just(Decimal("0.5")),  # boundary -- must be retained (if evidenced)
    st.just(Decimal("0.49")),  # just below -- must be excluded
    st.just(Decimal("0.51")),  # just above -- retained (if evidenced)
    st.just(Decimal("0.00")),
    st.just(Decimal("1.00")),
    st.decimals(
        min_value=Decimal("0.00"),
        max_value=Decimal("1.00"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ),
)

# Reference id lists of length 0..n so the evidence condition varies across
# both-empty, one-empty, and both-non-empty combinations.
_id_lists = st.lists(st.text(min_size=1, max_size=8), min_size=0, max_size=4)


@st.composite
def findings(draw):
    """Generate a list of Findings with unique ids, varying confidence around
    the 0.5 boundary, and varying evidence (line-item and passage references).

    The list length includes 0 (empty-input case), and because confidence and
    evidence vary independently, the generator naturally folds in lists where
    no Finding qualifies (all-excluded case, Req 6.5)."""
    n = draw(st.integers(min_value=0, max_value=8))
    items: list[Finding] = []
    for i in range(n):
        items.append(
            Finding(
                id=f"f-{i}",  # unique within the list
                type=draw(st.sampled_from(list(FindingType))),
                description=f"finding {i}",
                confidence=draw(_confidences),
                line_item_ids=draw(_id_lists),
                passage_ids=draw(_id_lists),
            )
        )
    return items


def _qualifies(f: Finding) -> bool:
    """Independent reference implementation of the inclusion rule."""
    return f.confidence >= _MIN_CONFIDENCE and (
        bool(f.line_item_ids) or bool(f.passage_ids)
    )


# Feature: boston-bill-party, Property 10: Skeptic retains a Finding exactly when it is confident and evidenced
# Validates: Requirements 6.2, 6.3, 6.4, 6.5
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(items=findings())
def test_skeptic_retains_iff_confident_and_evidenced(items: list[Finding]) -> None:
    retained = apply_skeptic(items)

    # Independently compute the expected retained subset (the "iff" rule),
    # preserving input order.
    expected = [f for f in items if _qualifies(f)]

    # The retained set equals the independently-computed qualifying subset.
    # Comparing the ordered lists simultaneously verifies the iff membership
    # rule and that order is preserved (Req 6.2, 6.3, 6.4).
    assert retained == expected

    # Per-finding iff, cross-checked against is_retained.
    retained_ids = {f.id for f in retained}
    for f in items:
        assert (f.id in retained_ids) == _qualifies(f)
        assert is_retained(f) == _qualifies(f)

    # Retained is a sub-sequence of input: nothing invented or duplicated.
    # Each retained element is the same object as the matching input element,
    # taken in order.
    it = iter(items)
    for r in retained:
        assert any(r is candidate for candidate in it)
    assert len(retained_ids) == len(retained)  # no duplicates

    # Empty input, or an input with no qualifying Finding, yields the empty
    # set (Req 6.5).
    if not items or not any(_qualifies(f) for f in items):
        assert retained == []

    # Boundary: a Finding with confidence exactly 0.5 and >=1 reference is
    # retained (inclusive threshold, Req 6.2/6.4).
    for f in items:
        if f.confidence == _MIN_CONFIDENCE and (f.line_item_ids or f.passage_ids):
            assert f.id in retained_ids
