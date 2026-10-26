"""User-facing errors never contain provider text; details go to the log.

Drives the real session bridge, the result endpoint, and the ask/translate
endpoints with a stub provider that fails like the Anthropic provider does
("Anthropic request failed with HTTP 401"). No real LLM or network.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

import sessions
from ask_endpoint_test import _call_post_json, _register_analyzed
from models import SupportedLanguage
from pipeline._graph_fixtures import (  # noqa: F401  (hermetic is autouse)
    Raise,
    Respond,
    StubKB,
    StubProvider,
    eval_bill_cases,
    hermetic,
)
from pipeline.graph import run_pipeline
from pipeline.state import PipelineState
from public_errors import (
    EXTRACTOR_MESSAGE,
    GENERIC_MESSAGE,
    PUBLIC_MESSAGES,
    WRITER_MESSAGE,
    public_error,
)
from result_endpoint_test import _call_get
from translate_endpoint_test import _register as _register_translation

PROVIDER_TEXT = "Anthropic request failed with HTTP 401"
LEAK_MARKERS = ("Anthropic", "HTTP 401", "401")


def _assert_friendly(message: str) -> None:
    assert message in PUBLIC_MESSAGES
    for marker in LEAK_MARKERS:
        assert marker not in message


@pytest.fixture(autouse=True)
def _clean():
    sessions.clear_sessions()
    yield
    sessions.clear_sessions()


def _run_session(provider, monkeypatch, *, session_id: str):
    """Run one session through the real bridge; return (session, queue items)."""
    kb = StubKB.match()
    monkeypatch.setattr(
        sessions, "run_pipeline", lambda *a, **k: run_pipeline(*a, kb=kb, **k)
    )
    case = eval_bill_cases()[0]

    async def main():
        state = PipelineState(session_id=session_id, language=SupportedLanguage.ES)
        session = sessions.Session(id=session_id, language=SupportedLanguage.ES, state=state)
        session.provider = provider
        session.pdf_source = case.pdf()
        sessions._SESSIONS[session_id] = session
        await sessions._run_session_pipeline(session)
        # Let call_soon_threadsafe callbacks from the worker thread land.
        for _ in range(5):
            await asyncio.sleep(0)
        items = []
        while not session.queue.empty():
            items.append(session.queue.get_nowait())
        return session, items

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(main())
    finally:
        loop.close()


@pytest.mark.parametrize(
    "script,failed_node,public_message",
    [
        ({"extractor": Raise(RuntimeError, PROVIDER_TEXT)}, "extractor", EXTRACTOR_MESSAGE),
        (
            {
                "extractor": Respond(eval_bill_cases()[0].extractor_json),
                "writer": Raise(RuntimeError, PROVIDER_TEXT),
            },
            "writer",
            WRITER_MESSAGE,
        ),
    ],
    ids=["extractor", "writer"],
)
def test_provider_failure_is_friendly_everywhere_and_logged(
    script, failed_node, public_message, monkeypatch, caplog
):
    caplog.set_level(logging.WARNING, logger="boston_bill_party")
    provider = StubProvider(script)
    session, items = _run_session(provider, monkeypatch, session_id=f"pf-{failed_node}")

    # Internal state: fixed node message, no provider text.
    assert session.state.error["failedNode"] == failed_node
    for marker in LEAK_MARKERS:
        assert marker not in session.state.error["message"]

    # SSE node_error event.
    errors = [data for kind, data in items if kind == "node_error"]
    assert errors == [{"failedNode": failed_node, "message": public_message}]

    # Result endpoint.
    status, payload = _call_get(f"/sessions/{session.id}/result")
    assert status == 200
    assert payload["status"] == "error"
    assert payload["error"] == {"failedNode": failed_node, "message": public_message}

    # The detail is in the server log, and the API key never is.
    assert PROVIDER_TEXT in caplog.text
    assert provider.api_key not in caplog.text


def test_unexpected_crash_is_generic(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="boston_bill_party")

    def boom(*args, **kwargs):
        raise RuntimeError("internal detail: /srv/app/rag/corpus missing")

    monkeypatch.setattr(sessions, "run_pipeline", boom)

    async def main():
        state = PipelineState(session_id="crash", language=SupportedLanguage.EN)
        session = sessions.Session(id="crash", language=SupportedLanguage.EN, state=state)
        session.provider = StubProvider({})
        session.pdf_source = None
        sessions._SESSIONS["crash"] = session
        await sessions._run_session_pipeline(session)
        return [session.queue.get_nowait() for _ in range(session.queue.qsize())]

    items = asyncio.run(main())
    assert items == [
        ("node_error", {"failedNode": "pipeline", "message": GENERIC_MESSAGE}),
        ("done", None),
    ]
    status, payload = _call_get("/sessions/crash/result")
    assert payload["error"] == {"failedNode": "pipeline", "message": GENERIC_MESSAGE}
    assert "corpus missing" in caplog.text


def test_public_error_maps_every_node():
    for node in ("extractor", "auditor", "rights_checker", "skeptic", "writer", "pipeline", None):
        result = public_error({"failedNode": node, "message": PROVIDER_TEXT})
        _assert_friendly(result["message"])
    assert public_error(None) == {"failedNode": "pipeline", "message": GENERIC_MESSAGE}


class _Failing:
    def complete(self, **kwargs):
        raise RuntimeError(PROVIDER_TEXT)


def test_ask_failure_hides_provider_text_and_logs_it(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="boston_bill_party")
    _register_analyzed("ask-pf")
    monkeypatch.setattr(sessions, "get_provider", lambda: _Failing())
    status, payload = _call_post_json("/sessions/ask-pf/ask", {"question": "Total?"})
    assert status == 502
    for marker in LEAK_MARKERS:
        assert marker not in payload["detail"]
    assert PROVIDER_TEXT in caplog.text


def test_translate_failure_hides_provider_text_and_logs_it(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="boston_bill_party")
    _register_translation("tr-pf", SupportedLanguage.HT)
    monkeypatch.setattr(sessions, "get_provider", lambda: _Failing())
    status, payload = _call_post_json(
        "/sessions/tr-pf/translate-letter", {"letter": "Dear billing office,"}
    )
    assert status == 502
    for marker in LEAK_MARKERS:
        assert marker not in payload["detail"]
    assert PROVIDER_TEXT in caplog.text
