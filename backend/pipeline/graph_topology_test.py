"""Topology tests for the LangGraph Pipeline_Graph (langgraph-migration task 5.1).

Covers the node set, entry node, edge set (with ``conditional`` flags), the
success-path execution order, Error_Router behavior for every node-fatal node,
node-local Rights_Checker failures, ``NODE_ORDER``/``TOTAL_NODES``, compile-once,
the Graph_State field set, and the absence of a checkpointer on the default graph.

Stub providers and stub knowledge bases only: no real LLM, no network (the
imported ``hermetic`` fixture is autouse and blocks IP sockets).

Requirements: 2.1, 4.1, 4.2, 4.3, 4.4, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2,
9.2, 10.2, 10.3, 10.4, 10.5, 10.7, 10.8.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import pytest
from langgraph.graph import END, START, StateGraph

import pipeline.graph as graph_mod
from config import NodeName
from models import RightsStatus
from pipeline._graph_fixtures import (  # noqa: F401  (hermetic is an autouse fixture)
    FAILING_NODE,
    Scenario,
    ScenarioKind,
    StubProvider,
    effective_case,
    eval_bill_cases,
    fresh_state,
    hermetic,
    make_kb,
    make_provider,
    scenario_environment,
)
from pipeline.graph import (
    NODE_ORDER,
    TOTAL_NODES,
    PipelineContext,
    get_compiled_graph,
    run_pipeline,
    to_graph_input,
)
from pipeline.state import NodeStatus, PipelineState

NODE_NAMES: list[str] = [n.value for n in NODE_ORDER]

EXPECTED_NODES = {START, *NODE_NAMES, END}

# (source, target, conditional) per the design's Error_Router wiring.
EXPECTED_EDGES = {
    (START, "extractor", False),
    ("extractor", "auditor", True),
    ("extractor", END, True),
    ("auditor", "rights_checker", True),
    ("auditor", END, True),
    ("rights_checker", "skeptic", False),
    ("skeptic", "writer", True),
    ("skeptic", END, True),
    ("writer", END, True),
}


def _case():
    return eval_bill_cases()[0]


@dataclass
class StreamRun:
    order: list[str]  # node keys yielded by stream_mode="updates", in order
    state: PipelineState  # live caller state (mirrored by the wrappers)
    provider: StubProvider


def _stream_run(scenario: Scenario) -> StreamRun:
    """Drive the compiled graph with ``stream(stream_mode="updates")``."""
    case = _case()
    state = fresh_state()
    graph_mod._seed_node_status(state)
    provider = make_provider(case, scenario)
    provider.watch(state)
    ctx = PipelineContext(
        provider=provider,
        kb=make_kb(scenario),
        pdf_source=effective_case(case, scenario).pdf(),
        live_state=state,
        progress=None,
    )
    order: list[str] = []
    with scenario_environment(scenario):
        for chunk in get_compiled_graph().stream(
            to_graph_input(state), context=ctx, stream_mode="updates"
        ):
            order.extend(chunk.keys())
    return StreamRun(order, state, provider)


# ---------------------------------------------------------------------------
# Static topology
# ---------------------------------------------------------------------------


def test_node_set_is_exactly_five_pipeline_nodes_plus_markers() -> None:
    graph = get_compiled_graph().get_graph()
    assert set(graph.nodes) == EXPECTED_NODES


def test_entry_node_is_extractor() -> None:
    graph = get_compiled_graph().get_graph()
    starts = [e.target for e in graph.edges if e.source == START]
    assert starts == ["extractor"]


def test_edge_set_and_conditional_flags_match_design() -> None:
    graph = get_compiled_graph().get_graph()
    edges = [(e.source, e.target, e.conditional) for e in graph.edges]
    assert len(edges) == len(set(edges))
    assert set(edges) == EXPECTED_EDGES


def test_success_path_successor_follows_node_order() -> None:
    graph = get_compiled_graph().get_graph()
    for i, name in enumerate(NODE_NAMES):
        expected = NODE_NAMES[i + 1] if i + 1 < len(NODE_NAMES) else END
        non_end = {e.target for e in graph.edges if e.source == name and e.target != END}
        assert non_end == ({expected} if expected != END else set())


def test_node_order_and_total_nodes_constants() -> None:
    assert NODE_ORDER == (
        NodeName.EXTRACTOR,
        NodeName.AUDITOR,
        NodeName.RIGHTS_CHECKER,
        NodeName.SKEPTIC,
        NodeName.WRITER,
    )
    assert TOTAL_NODES == 5
    assert NODE_NAMES == ["extractor", "auditor", "rights_checker", "skeptic", "writer"]


def test_graph_state_fields_equal_pipeline_state_fields() -> None:
    compiled = get_compiled_graph()
    assert compiled.builder.state_schema is PipelineState
    assert set(compiled.builder.channels) == set(PipelineState.model_fields)
    assert len(PipelineState.model_fields) == 11


def test_default_graph_has_no_checkpointer() -> None:
    assert get_compiled_graph().checkpointer is None


# ---------------------------------------------------------------------------
# Success run
# ---------------------------------------------------------------------------


def test_success_run_executes_each_node_once_in_order() -> None:
    run = _stream_run(Scenario(ScenarioKind.SUCCESS))
    assert run.order == NODE_NAMES
    assert all(run.state.node_status[n] == NodeStatus.COMPLETED for n in NODE_NAMES)
    assert run.state.error is None
    assert run.state.result is not None


def test_success_run_through_run_pipeline() -> None:
    case = _case()
    scenario = Scenario(ScenarioKind.SUCCESS)
    state = fresh_state()
    transitions: list[tuple[str, NodeStatus]] = []
    with scenario_environment(scenario):
        returned = run_pipeline(
            state,
            make_provider(case, scenario),
            case.pdf(),
            lambda node, status, _s: transitions.append((node, status)),
            kb=make_kb(scenario),
        )
    assert returned is state
    assert state.error is None
    assert {n: state.node_status[n] for n in NODE_NAMES} == {
        n: NodeStatus.COMPLETED for n in NODE_NAMES
    }
    assert transitions == [
        (n, s) for n in NODE_NAMES for s in (NodeStatus.RUNNING, NodeStatus.COMPLETED)
    ]


# ---------------------------------------------------------------------------
# Node-fatal routing
# ---------------------------------------------------------------------------

FATAL_CASES = [
    pytest.param(Scenario(ScenarioKind.EXTRACTOR_FAIL, variant="invalid_json"), id="extractor"),
    pytest.param(Scenario(ScenarioKind.AUDITOR_FAIL, message="audit boom"), id="auditor"),
    pytest.param(Scenario(ScenarioKind.SKEPTIC_FAIL, message="skeptic boom"), id="skeptic"),
    pytest.param(Scenario(ScenarioKind.WRITER_FAIL, variant="raise"), id="writer"),
]


@pytest.mark.parametrize("scenario", FATAL_CASES)
def test_fatal_error_routes_to_end(scenario: Scenario) -> None:
    failing = FAILING_NODE[scenario.kind]
    idx = NODE_NAMES.index(failing)
    earlier, later = NODE_NAMES[:idx], NODE_NAMES[idx + 1 :]

    run = _stream_run(scenario)

    # Graph stopped at the failing node: no later node produced an update.
    assert run.order == NODE_NAMES[: idx + 1]
    status = run.state.node_status
    assert status[failing] == NodeStatus.ERROR
    assert all(status[n] == NodeStatus.COMPLETED for n in earlier)
    assert all(status[n] == NodeStatus.PENDING for n in later)
    assert [n for n in NODE_NAMES if status[n] == NodeStatus.ERROR] == [failing]
    assert run.state.error is not None
    assert run.state.error["failedNode"] == failing
    assert run.state.result is None
    # Later nodes made zero provider calls.
    assert not [c for c in run.provider.calls if c.node in later or c.running_node in later]
    assert run.provider.unexpected_calls == []


def test_generic_fatal_message_is_str_of_exception() -> None:
    run = _stream_run(Scenario(ScenarioKind.AUDITOR_FAIL, message="audit boom"))
    assert run.state.error == {"failedNode": "auditor", "message": "audit boom"}


# ---------------------------------------------------------------------------
# Node-local Rights_Checker failures
# ---------------------------------------------------------------------------

RC_CASES = [
    pytest.param(ScenarioKind.RC_RAISE, id="retrieval-error"),
    pytest.param(ScenarioKind.RC_EMPTY, id="empty-result"),
    pytest.param(ScenarioKind.RC_TIMEOUT, id="timeout"),
    pytest.param(ScenarioKind.RC_UNEXPECTED, id="unexpected-exception"),
]


@pytest.mark.parametrize("kind", RC_CASES)
def test_rights_checker_failure_is_node_local(kind: ScenarioKind) -> None:
    run = _stream_run(Scenario(kind, message="rights boom"))
    assert run.order == NODE_NAMES  # routed on to skeptic and writer
    assert run.state.rights_status == RightsStatus.UNAVAILABLE
    for n in ("rights_checker", "skeptic", "writer"):
        assert run.state.node_status[n] == NodeStatus.COMPLETED
    assert run.state.error is None
    assert run.state.result is not None


# ---------------------------------------------------------------------------
# Compile once
# ---------------------------------------------------------------------------


@pytest.fixture
def compile_spy(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Reset the cached graph and count ``StateGraph.compile`` calls.

    monkeypatch restores ``_COMPILED`` and ``StateGraph.compile`` afterwards.
    """
    monkeypatch.setattr(graph_mod, "_COMPILED", None)
    calls: list[int] = []
    real_compile = StateGraph.compile

    def spy(self, *args, **kwargs):
        calls.append(1)
        time.sleep(0.05)  # widen the race window for the concurrent case
        return real_compile(self, *args, **kwargs)

    monkeypatch.setattr(StateGraph, "compile", spy)
    return calls


def test_compile_once_sequential(compile_spy: list[int]) -> None:
    first = get_compiled_graph()
    second = get_compiled_graph()
    case = _case()
    scenario = Scenario(ScenarioKind.SUCCESS)
    with scenario_environment(scenario):
        for _ in range(2):
            run_pipeline(
                fresh_state(), make_provider(case, scenario), case.pdf(), kb=make_kb(scenario)
            )
    assert first is second is get_compiled_graph()
    assert len(compile_spy) == 1


def test_compile_once_concurrent_first_calls(compile_spy: list[int]) -> None:
    barrier = threading.Barrier(4)
    results: list[object] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        graph = get_compiled_graph()
        with lock:
            results.append(graph)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(results) == 4
    assert all(g is results[0] for g in results)
    assert len(compile_spy) == 1
