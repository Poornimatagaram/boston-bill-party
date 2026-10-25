"""Equivalence and invariant properties for the LangGraph-backed pipeline.

langgraph-migration spec, task 5.2 (Property 1). Later optional properties
(5-12) are meant to be appended to this module; the shared helpers below
(``money_strings``, ``assert_equivalent``) are written for reuse.

Every run uses stub providers and stub knowledge bases from
``pipeline._graph_fixtures``; no real LLM or network is touched. ``hermetic``
is imported so it applies autouse to every test here; inside Hypothesis
examples, ``run_one`` / ``run_both`` additionally apply
``scenario_environment`` per call, so patches are reset per example.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

import pytest
from hypothesis import HealthCheck, given, settings

from models import SupportedLanguage
from pipeline._graph_fixtures import (  # noqa: F401  (hermetic is an autouse fixture)
    DualRun,
    Scenario,
    ScenarioKind,
    bill_cases,
    eval_bill_cases,
    hermetic,
    languages,
    run_both,
    scenarios,
)
from pipeline.state import PipelineState

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

#: Hypothesis settings shared by the properties in this module.
PROPERTY_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


def money_strings(state: PipelineState) -> dict[str, Optional[str]]:
    """Map every money field path in ``state`` to ``str(value)`` (or ``None``).

    Covers line-item ``unit_price``/``amount``, ``stated_total``, the Sum_Check
    ``computed_total``, and Finding ``computed_total``/``stated_total`` in both
    ``findings`` and ``retained_findings``. ``str()`` exposes the Decimal scale
    (``"1.5"`` vs ``"1.50"``), which ``==`` alone would not. Asserts every
    present amount is a ``Decimal`` (never ``float``, Req 2.5).
    """
    out: dict[str, Optional[str]] = {}

    def put(path: str, value: object) -> None:
        if value is not None:
            assert isinstance(value, Decimal), f"{path} is {type(value).__name__}, not Decimal"
        out[path] = None if value is None else str(value)

    bill = state.bill
    if bill is not None:
        put("bill.stated_total", bill.stated_total)
        for i, item in enumerate(bill.line_items):
            put(f"bill.line_items[{i}].unit_price", item.unit_price)
            put(f"bill.line_items[{i}].amount", item.amount)
    if state.sum_check is not None:
        put("sum_check.computed_total", state.sum_check.computed_total)
    for field in ("findings", "retained_findings"):
        for i, finding in enumerate(getattr(state, field)):
            put(f"{field}[{i}].computed_total", finding.computed_total)
            put(f"{field}[{i}].stated_total", finding.stated_total)
    return out


def assert_equivalent(run: DualRun) -> None:
    """Property 1 assertions for one dual run (new graph vs. reference)."""
    # Identity: each runner returns the caller's own instance (Req 2.3, 2.6).
    assert run.new.returned is run.new.caller_state
    assert run.ref.returned is run.ref.caller_state

    new_state, ref_state = run.new.caller_state, run.ref.caller_state

    # Field-by-field equality, incl. node_status, rights_status, error and
    # partial writes such as raw_text on an Extractor failure (Req 3.7).
    assert new_state.model_dump() == ref_state.model_dump()

    # Decimal value AND scale, and Decimal-ness, of every money field (Req 2.5).
    assert money_strings(new_state) == money_strings(ref_state)

    # Same ordered (node, status) progress transitions (Req 3.7, 7.2).
    assert run.new.transitions == run.ref.transitions


# ---------------------------------------------------------------------------
# Property 1: Model-based equivalence with the Pre_Migration_Runner
# ---------------------------------------------------------------------------

_LANGS = tuple(SupportedLanguage)


@pytest.mark.parametrize("kind", tuple(ScenarioKind), ids=lambda k: k.value)
@pytest.mark.parametrize("index", range(len(eval_bill_cases())), ids=lambda i: f"bill_{i + 1:02d}")
def test_equivalence_eval_bills(index: int, kind: ScenarioKind) -> None:
    # Feature: langgraph-migration, Property 1: Model-based equivalence with the Pre_Migration_Runner
    """**Validates: Requirements 2.3, 2.5, 2.6, 3.7, 3.9, 4.2, 5.6, 7.2, 9.2**

    The 10 eval-set bills under every scenario; languages rotate so all four
    are exercised.
    """
    case = eval_bill_cases()[index]
    language = _LANGS[(index + list(ScenarioKind).index(kind)) % len(_LANGS)]
    assert_equivalent(run_both(case, Scenario(kind=kind), language))


@PROPERTY_SETTINGS
@given(case=bill_cases(), scenario=scenarios(), language=languages())
def test_equivalence_property(case, scenario, language) -> None:
    # Feature: langgraph-migration, Property 1: Model-based equivalence with the Pre_Migration_Runner
    """**Validates: Requirements 2.3, 2.5, 2.6, 3.7, 3.9, 4.2, 5.6, 7.2, 9.2**

    Eval-set and Hypothesis-generated bills, any language, any scenario
    (success, Rights_Checker failures, and each node-fatal failure).
    """
    assert_equivalent(run_both(case, scenario, language))
