"""Skeptic filter for the Boston Bill Party pipeline.

The Skeptic is a pure, deterministic filter -- never the LLM -- that removes
weak or unevidenced Findings before anything reaches the Writer (Req 6.1).

Inclusion rule (Property 10):
    A Finding is RETAINED if and only if
        confidence >= 0.5                                  (Req 6.2)
      AND
        it references at least one Line_Item OR at least   (Req 6.3, 6.4)
        one knowledge-base passage.
    Otherwise it is excluded.

Boundary: a confidence of exactly ``0.5`` is RETAINED -- the comparison is
inclusive (``>=``). The confidence bar is expressed as ``Decimal("0.5")`` so
it compares exactly against the ``Decimal`` confidence carried on a Finding.

The filter is order-preserving (retained Findings keep their input order),
performs no I/O, and never mutates its inputs. An empty input -- or a set in
which no Finding qualifies -- yields an empty output (Req 6.5).
"""

from __future__ import annotations

from decimal import Decimal

from models import Finding

# Minimum confidence to retain a Finding; the comparison is inclusive (Req 6.2).
_MIN_CONFIDENCE = Decimal("0.5")


def is_retained(finding: Finding) -> bool:
    """Return whether a single Finding passes the Skeptic's inclusion rule.

    A Finding is retained iff its ``confidence`` is at least ``0.5`` (inclusive)
    and it references at least one Line_Item or at least one knowledge-base
    passage (Req 6.2, 6.3, 6.4). All other Findings are excluded.

    Args:
        finding: The Finding to evaluate.

    Returns:
        ``True`` to keep the Finding, ``False`` to drop it.
    """
    has_confidence = finding.confidence >= _MIN_CONFIDENCE
    has_evidence = bool(finding.line_item_ids) or bool(finding.passage_ids)
    return has_confidence and has_evidence


def apply_skeptic(findings: list[Finding]) -> list[Finding]:
    """Filter a list of Findings down to those the Skeptic retains.

    Pure and deterministic: returns a new list containing exactly the Findings
    for which :func:`is_retained` is ``True``, preserving their input order and
    leaving the input list and its elements unmodified. An empty input, or an
    input in which no Finding qualifies, yields an empty list (Req 6.5).

    Args:
        findings: The combined auditor and rights-checker Findings.

    Returns:
        The retained subset, in input order.
    """
    return [finding for finding in findings if is_retained(finding)]
