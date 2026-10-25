"""Sanity tests for ``pipeline/_graph_fixtures.py`` (langgraph-migration task 2.1).

These pin that each fixture scenario actually drives the failure path it is
named for, so the equivalence properties built on these fixtures cannot pass
vacuously (for example, a "writer failure" scenario that never fails). They
also check that the current ``run_pipeline`` and the frozen reference runner
agree on the eval-set bills. Stub providers and stub KBs only; no network.
"""

from __future__ import annotations

import socket

import pytest

from models import FindingType, RightsStatus
from pipeline._graph_fixtures import (  # noqa: F401  (hermetic is autouse)
    FAILING_NODE,
    RIGHTS_CHECKER_FAILURES,
    VARIANTS,
    NetworkBlockedError,
    Scenario,
    ScenarioKind,
    eval_bill_cases,
    hermetic,
    run_both,
)
from pipeline.state import NodeStatus

_NODES = ("extractor", "auditor", "rights_checker", "skeptic", "writer")
_CASE = eval_bill_cases()[2]  # bill_03: duplicate charge, non-empty query


def test_eval_bill_cases_are_the_ten_synthetic_bills():
    cases = eval_bill_cases()
    assert [c.name for c in cases] == [f"bill_{i:02d}" for i in range(1, 11)]
    assert all("SYNTHETIC" in c.pdf_text for c in cases)


@pytest.mark.parametrize("case", eval_bill_cases(), ids=lambda c: c.name)
def test_runners_agree_on_eval_bills(case):
    run = run_both(case, Scenario(ScenarioKind.SUCCESS))
    assert run.new.returned is run.new.caller_state
    assert run.new.returned.model_dump() == run.ref.returned.model_dump()
    assert run.new.transitions == run.ref.transitions
    assert run.new.returned.error is None
    assert run.new.returned.result is not None


@pytest.mark.parametrize("kind", RIGHTS_CHECKER_FAILURES, ids=lambda k: k.value)
def test_rights_checker_scenarios_are_node_local(kind):
    run = run_both(_CASE, Scenario(kind, message="rc boom"))
    for record in (run.new, run.ref):
        state = record.returned
        assert state.rights_status == RightsStatus.UNAVAILABLE
        assert state.error is None
        assert all(state.node_status[n] == NodeStatus.COMPLETED for n in _NODES)
        assert not any(f.type == FindingType.RIGHTS_ISSUE for f in state.findings)
        assert record.provider.unexpected_calls == []
    assert run.new.returned.model_dump() == run.ref.returned.model_dump()


_FATAL_CASES = [
    (kind, variant)
    for kind in FAILING_NODE
    for variant in VARIANTS.get(kind, ("",))
]


@pytest.mark.parametrize(
    "kind,variant", _FATAL_CASES, ids=[f"{k.value}-{v or 'default'}" for k, v in _FATAL_CASES]
)
def test_fatal_scenarios_fail_the_named_node(kind, variant):
    failing = FAILING_NODE[kind]
    run = run_both(_CASE, Scenario(kind, message="fatal boom", variant=variant))
    for record in (run.new, run.ref):
        state = record.returned
        assert state.error is not None
        assert state.error["failedNode"] == failing
        assert state.node_status[failing] == NodeStatus.ERROR
        assert record.transitions[-1] == (failing, NodeStatus.ERROR)
        later = _NODES[_NODES.index(failing) + 1 :]
        assert all(state.node_status[n] == NodeStatus.PENDING for n in later)
    assert run.new.returned.model_dump() == run.ref.returned.model_dump()
    assert run.new.transitions == run.ref.transitions


def test_provider_records_running_node_and_holds_sentinel_key():
    run = run_both(_CASE, Scenario(ScenarioKind.SUCCESS))
    provider = run.new.provider
    assert provider.api_key.startswith("sk-test-sentinel-")
    assert {c.node for c in provider.calls} == {"extractor", "writer"}
    assert all(c.running_node == c.node for c in provider.calls)


def test_ip_network_is_blocked():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlockedError):
            sock.connect(("127.0.0.1", 9))
    finally:
        sock.close()
