"""Event_Sequence tests through the real Session_Bridge (langgraph-migration task 5.3).

Both runners (the migrated ``pipeline.graph.run_pipeline`` and the frozen
``pipeline._reference_runner.run_pipeline_reference``) are driven through the
real ``sessions._run_session_pipeline`` coroutine, which offloads the runner via
``asyncio.to_thread`` and feeds a real ``asyncio.Queue`` through
``sessions._make_progress_bridge``. The runner is swapped by patching the
``run_pipeline`` name that ``sessions`` imported, wrapped in a ``partial`` that
injects the scenario's stub knowledge base.

No pytest-asyncio: each coroutine runs on one shared module-level event loop
(see ``_run_on_loop``) from a sync test.
Stub providers and stub KBs only; no real LLM, no network (``hermetic`` autouse).
"""

from __future__ import annotations

import asyncio
import functools
import threading
from typing import Any, Callable, Optional

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import pipeline._reference_runner as reference_mod
import pipeline.graph as graph_mod
import rag.knowledge_base as kb_mod
import sessions
from models import SupportedLanguage
from pipeline._graph_fixtures import (  # noqa: F401  (hermetic is an autouse fixture)
    RIGHTS_CHECKER_FAILURES,
    WRITER_TEXT,
    BillCase,
    Scenario,
    ScenarioKind,
    StubKB,
    StubProvider,
    Respond,
    bill_cases,
    effective_case,
    eval_bill_cases,
    hermetic,
    languages,
    make_kb,
    make_provider,
    patched,
    scenario_environment,
    scenarios,
)
from pipeline.state import PipelineState

QueueItem = tuple[str, Optional[dict]]

PROGRESS_KEYS = {"activeNode", "completedNodes", "completedCount", "total"}
NODE_ERROR_KEYS = {"failedNode", "message"}
NODE_ORDER_NAMES = [n.value for n in graph_mod.NODE_ORDER]

#: The four Property 2 scenario families (all six Rights_Checker failure modes).
PROPERTY_2_KINDS: tuple[ScenarioKind, ...] = (
    ScenarioKind.SUCCESS,
    *RIGHTS_CHECKER_FAILURES,
    ScenarioKind.EXTRACTOR_FAIL,
    ScenarioKind.WRITER_FAIL,
)


# One event loop shared by every test in this module, mirroring production
# (a single long-lived uvicorn loop). Creating a fresh loop per example with
# ``asyncio.run`` was observed on CPython 3.14.3 to occasionally resume the
# ``to_thread`` await before the worker's ``call_soon_threadsafe`` callbacks ran
# (for both runners alike), which is a harness artifact, not bridge behavior.
_LOOP: Optional[asyncio.AbstractEventLoop] = None


def _run_on_loop(coro: Any) -> Any:
    global _LOOP
    if _LOOP is None or _LOOP.is_closed():
        _LOOP = asyncio.new_event_loop()
    return _LOOP.run_until_complete(coro)


@pytest.fixture(scope="module", autouse=True)
def _close_shared_loop():
    yield
    global _LOOP
    if _LOOP is not None and not _LOOP.is_closed():
        _LOOP.run_until_complete(_LOOP.shutdown_default_executor())
        _LOOP.close()
    _LOOP = None


def _make_session(
    provider: Any, pdf_source: Any, language: SupportedLanguage, session_id: str
) -> sessions.Session:
    """Build a Session the way ``create_session`` does, without registering it."""
    state = PipelineState(session_id=session_id, language=language)
    session = sessions.Session(id=session_id, language=language, state=state)
    session.provider = provider
    session.pdf_source = pdf_source
    return session


def _drain(queue: "asyncio.Queue[QueueItem]") -> list[QueueItem]:
    items: list[QueueItem] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def _run_through_bridge(
    runner: Callable[..., PipelineState],
    case: BillCase,
    scenario: Scenario,
    language: SupportedLanguage,
) -> list[QueueItem]:
    """Run ``runner`` via the real ``_run_session_pipeline`` and return the queue items."""
    provider = make_provider(case, scenario)
    kb = make_kb(scenario)
    pdf = effective_case(case, scenario).pdf()

    async def main() -> list[QueueItem]:
        session = _make_session(provider, pdf, language, "s-events")
        provider.watch(session.state)
        await sessions._run_session_pipeline(session)
        return _drain(session.queue)

    with scenario_environment(scenario), patched(
        [(sessions, "run_pipeline", functools.partial(runner, kb=kb))]
    ):
        return _run_on_loop(main())


def _assert_shapes(items: list[QueueItem]) -> None:
    assert items, "queue must not be empty"
    assert items[-1] == ("done", None)
    assert [t for t, _ in items].count("done") == 1
    for event_type, data in items[:-1]:
        assert event_type in ("progress", "node_error")
        assert isinstance(data, dict)
        if event_type == "progress":
            assert set(data) == PROGRESS_KEYS
            assert data["total"] == 5
            assert data["completedCount"] == len(data["completedNodes"])
            assert 0 <= data["completedCount"] <= 5
            # completedNodes is a prefix-ordered subset of the pipeline order.
            assert data["completedNodes"] == [
                n for n in NODE_ORDER_NAMES if n in data["completedNodes"]
            ]
            assert data["activeNode"] is None or data["activeNode"] in NODE_ORDER_NAMES
        else:
            assert set(data) == NODE_ERROR_KEYS


def _expected_counts(kind: ScenarioKind) -> tuple[int, int]:
    """(progress events, node_error events) per scenario family (Req 7.7)."""
    if kind is ScenarioKind.EXTRACTOR_FAIL:
        return 1, 1
    if kind is ScenarioKind.WRITER_FAIL:
        return 9, 1
    return 10, 0  # success and every Rights_Checker failure


# Feature: langgraph-migration, Property 2: Event_Sequence equivalence through the Session_Bridge
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(case=bill_cases(), scenario=scenarios(kinds=PROPERTY_2_KINDS), language=languages())
def test_event_sequence_matches_reference_through_bridge(
    case: BillCase, scenario: Scenario, language: SupportedLanguage
) -> None:
    """**Validates: Requirements 6.7, 7.7, 7.8, 7.9, 8.4, 10.6**"""
    new_items = _run_through_bridge(graph_mod.run_pipeline, case, scenario, language)
    ref_items = _run_through_bridge(
        reference_mod.run_pipeline_reference, case, scenario, language
    )

    assert new_items == ref_items
    _assert_shapes(new_items)

    progress_count, error_count = _expected_counts(scenario.kind)
    types = [t for t, _ in new_items[:-1]]
    assert types.count("progress") == progress_count
    assert types.count("node_error") == error_count
    if error_count:
        assert types[-1] == "node_error"

    if error_count == 0:
        # Req 8.4: one start + one completion update per node, in pipeline order.
        progress = [d for t, d in new_items if t == "progress"]
        for i, node in enumerate(NODE_ORDER_NAMES):
            started, completed = progress[2 * i], progress[2 * i + 1]
            assert started["activeNode"] == node
            assert started["completedNodes"] == NODE_ORDER_NAMES[:i]
            assert completed["activeNode"] is None
            assert completed["completedNodes"] == NODE_ORDER_NAMES[: i + 1]


def test_eval_bills_success_event_sequence_matches_reference() -> None:
    """Eval-set bills explicitly, success scenario (companion to Property 2)."""
    scenario = Scenario(ScenarioKind.SUCCESS)
    for case in eval_bill_cases():
        new_items = _run_through_bridge(
            graph_mod.run_pipeline, case, scenario, SupportedLanguage.EN
        )
        ref_items = _run_through_bridge(
            reference_mod.run_pipeline_reference, case, scenario, SupportedLanguage.EN
        )
        assert new_items == ref_items, case.name
        _assert_shapes(new_items)


def test_writer_running_event_is_queued_while_writer_blocks() -> None:
    """Req 7.5: the Writer's RUNNING progress reaches the queue while the Writer is blocked."""
    case = eval_bill_cases()[0]
    entered = threading.Event()
    release = threading.Event()

    def blocking_writer(system: str, user: str) -> str:
        entered.set()
        assert release.wait(timeout=10), "test never released the writer"
        return WRITER_TEXT

    provider = StubProvider(
        {"extractor": Respond(case.extractor_json), "writer": blocking_writer}
    )
    kb = StubKB.match()

    async def main() -> tuple[list[QueueItem], list[QueueItem]]:
        session = _make_session(provider, case.pdf(), SupportedLanguage.EN, "s-block")
        task = asyncio.create_task(sessions._run_session_pipeline(session))
        seen: list[QueueItem] = []
        try:
            while True:
                item = await asyncio.wait_for(session.queue.get(), timeout=10)
                seen.append(item)
                assert item[0] != "done", "pipeline finished before writer RUNNING was seen"
                if item[0] == "progress" and item[1]["activeNode"] == "writer":
                    break
            # Writer is still blocked: the event was delivered mid-node.
            assert entered.is_set()
            assert not task.done()
            assert session.state.result is None
        finally:
            release.set()
        await asyncio.wait_for(task, timeout=10)
        return seen, _drain(session.queue)

    with patched(
        [(sessions, "run_pipeline", functools.partial(graph_mod.run_pipeline, kb=kb))]
    ):
        before, after = _run_on_loop(main())

    writer_running = before[-1][1]
    assert writer_running["completedNodes"] == NODE_ORDER_NAMES[:4]
    items = before + after
    _assert_shapes(items)
    assert [t for t, _ in items].count("progress") == 10
    assert items[-2][1]["completedNodes"] == NODE_ORDER_NAMES


def test_kb_build_failure_yields_node_error_then_done(monkeypatch) -> None:
    """Req 7.9: a KB build failure outside any node gives ``node_error`` then ``done``."""
    case = eval_bill_cases()[0]
    provider = StubProvider({"extractor": Respond(case.extractor_json)})

    class BrokenKnowledgeBase:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise RuntimeError("synthetic corpus load failure")

    monkeypatch.setattr(graph_mod, "_KNOWLEDGE_BASE", None)
    monkeypatch.setattr(kb_mod, "KnowledgeBase", BrokenKnowledgeBase)
    # Real default path: sessions calls run_pipeline(state, provider, pdf, progress), kb=None.
    monkeypatch.setattr(sessions, "run_pipeline", graph_mod.run_pipeline)

    async def main() -> tuple[sessions.Session, list[QueueItem]]:
        session = _make_session(provider, case.pdf(), SupportedLanguage.EN, "s-kb")
        await sessions._run_session_pipeline(session)
        return session, _drain(session.queue)

    session, items = _run_on_loop(main())

    assert [t for t, _ in items] == ["node_error", "done"]
    assert items[-1] == ("done", None)
    assert set(items[0][1]) == NODE_ERROR_KEYS
    assert items[0][1]["failedNode"] == "pipeline"
    assert session.status is sessions.SessionStatus.ERROR
    assert provider.calls == []  # no node ran
    assert graph_mod._KNOWLEDGE_BASE is None
