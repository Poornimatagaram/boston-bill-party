"""Verification tests for POST /sessions/{id}/ask (task 15.1).

No real LLM runs: each test builds a :class:`sessions.Session` with a
hand-constructed :class:`PipelineState` and registers it directly in the session
registry, and ``sessions.get_provider`` is monkeypatched to return a mock
provider. Requests are driven through the ASGI app with a tiny in-process client
(the same approach as ``confirm_endpoint_test.py`` / ``result_endpoint_test.py``
— Starlette's ``TestClient`` needs ``httpx``, which these tasks do not add).

Covered (design "HTTP API"; Req 10.3 length bound, Req 10.6/10.7 answer/timeout):
* unknown session id                 -> 404
* empty / whitespace-only question   -> 400 (Req 10.3)
* question > 500 characters          -> 400 (Req 10.3)
* no bill extracted yet              -> 409 "analysis not ready"
* valid question on an analyzed bill -> 200 {answer}, provider called with the
                                        question, a 30s timeout, and context that
                                        mentions THIS bill (Req 10.6)
* provider raises                    -> 502 (Req 10.7)
* provider exceeds 30s (hangs)       -> 504 timeout (Req 10.6, 10.7)
"""

from __future__ import annotations

import json
from decimal import Decimal

import anyio
import pytest

import main
import sessions
from main import MAX_QUESTION_CHARS
from models import (
    AnalysisResult,
    ExtractedBill,
    Finding,
    FindingType,
    LineItem,
    SupportedLanguage,
)
from pipeline.state import PipelineState
from sessions import Session, SessionStatus


def _call_post_json(path: str, body_obj) -> tuple[int, object]:
    """Send one POST request with a JSON body through the ASGI app.

    Returns ``(status, parsed_json)`` mirroring the other endpoint tests.
    """
    raw_body = json.dumps(body_obj).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(raw_body)).encode()),
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }

    captured: dict = {"status": None, "body": bytearray()}
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw_body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
        elif message["type"] == "http.response.body":
            captured["body"] += message.get("body", b"")

    async def drive():
        await main.app(scope, receive, send)

    anyio.run(drive)

    data = bytes(captured["body"])
    try:
        payload = json.loads(data) if data else None
    except json.JSONDecodeError:
        payload = data.decode(errors="replace")
    return captured["status"], payload


class _MockProvider:
    """Records the last ``complete`` call and returns a canned answer."""

    def __init__(self, answer: str = "This is the answer about your bill."):
        self.answer = answer
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, timeout_s: float, node) -> str:
        self.calls.append(
            {"system": system, "user": user, "timeout_s": timeout_s, "node": node}
        )
        return self.answer


def _register_analyzed(session_id: str) -> Session:
    """Register a session whose state holds an extracted bill + findings + result."""
    state = PipelineState(session_id=session_id, language=SupportedLanguage.EN)
    state.bill = ExtractedBill(
        provider_name="Boston General Hospital",
        line_items=[
            LineItem(id="l1", description="MRI brain scan", amount=Decimal("1200.00")),
            LineItem(id="l2", description="Consultation", amount=Decimal("250.00")),
        ],
        stated_total=Decimal("1450.00"),
    )
    finding = Finding(
        id="f1",
        type=FindingType.DUPLICATE_CHARGE,
        description="The consultation appears to be billed twice.",
        confidence=Decimal("0.8"),
        line_item_ids=["l2"],
    )
    state.findings = [finding]
    state.retained_findings = [finding]
    state.result = AnalysisResult(
        summary_entries=["A duplicate consultation charge was detected."],
        appeal_letter="Dear provider, I contest the duplicate consultation charge.",
        retained_findings=[finding],
        language=SupportedLanguage.EN,
    )
    session = Session(
        id=session_id,
        language=SupportedLanguage.EN,
        state=state,
        status=SessionStatus.COMPLETED,
    )
    sessions._SESSIONS[session_id] = session
    return session


@pytest.fixture(autouse=True)
def _clean_registry():
    sessions.clear_sessions()
    yield
    sessions.clear_sessions()


def test_unknown_session_returns_404(monkeypatch):
    monkeypatch.setattr(sessions, "get_provider", lambda: _MockProvider())
    status, payload = _call_post_json(
        "/sessions/does-not-exist/ask", {"question": "How much is the MRI?"}
    )
    assert status == 404
    assert "does-not-exist" in payload["detail"]


def test_empty_question_rejected(monkeypatch):
    _register_analyzed("ask-empty")
    monkeypatch.setattr(sessions, "get_provider", lambda: _MockProvider())
    status, payload = _call_post_json("/sessions/ask-empty/ask", {"question": "   "})
    assert status == 400
    assert "empty" in payload["detail"].lower()


def test_question_over_max_length_rejected(monkeypatch):
    _register_analyzed("ask-toolong")
    monkeypatch.setattr(sessions, "get_provider", lambda: _MockProvider())
    too_long = "a" * (MAX_QUESTION_CHARS + 1)
    status, payload = _call_post_json(
        "/sessions/ask-toolong/ask", {"question": too_long}
    )
    assert status == 400
    assert str(MAX_QUESTION_CHARS) in payload["detail"]


def test_no_bill_returns_409(monkeypatch):
    state = PipelineState(session_id="ask-notready", language=SupportedLanguage.EN)
    session = Session(
        id="ask-notready",
        language=SupportedLanguage.EN,
        state=state,
        status=SessionStatus.RUNNING,
    )
    sessions._SESSIONS["ask-notready"] = session
    monkeypatch.setattr(sessions, "get_provider", lambda: _MockProvider())
    status, payload = _call_post_json(
        "/sessions/ask-notready/ask", {"question": "What is my total?"}
    )
    assert status == 409
    assert "not ready" in payload["detail"].lower()


def test_valid_question_returns_grounded_answer(monkeypatch):
    _register_analyzed("ask-ok")
    provider = _MockProvider(answer="Your MRI brain scan is $1200.00.")
    monkeypatch.setattr(sessions, "get_provider", lambda: provider)

    status, payload = _call_post_json(
        "/sessions/ask-ok/ask", {"question": "How much is the MRI?"}
    )
    assert status == 200, payload
    assert payload["answer"] == "Your MRI brain scan is $1200.00."

    # The provider was called with the user's question and a 30s timeout bound.
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["user"] == "How much is the MRI?"
    assert call["timeout_s"] == 30.0
    # The system context grounds the answer in THIS bill: provider, a line item,
    # and the detected finding all appear in the constructed context.
    system = call["system"]
    assert "Boston General Hospital" in system
    assert "MRI brain scan" in system
    assert "duplicate" in system.lower()


def test_question_is_trimmed_before_submission(monkeypatch):
    _register_analyzed("ask-trim")
    provider = _MockProvider()
    monkeypatch.setattr(sessions, "get_provider", lambda: provider)

    status, payload = _call_post_json(
        "/sessions/ask-trim/ask", {"question": "  Is there a duplicate charge?  "}
    )
    assert status == 200, payload
    assert provider.calls[0]["user"] == "Is there a duplicate charge?"


def test_provider_failure_returns_502(monkeypatch):
    _register_analyzed("ask-fail")

    class _Boom:
        def complete(self, **kwargs):
            raise RuntimeError("provider exploded")

    monkeypatch.setattr(sessions, "get_provider", lambda: _Boom())
    status, payload = _call_post_json(
        "/sessions/ask-fail/ask", {"question": "What is my total?"}
    )
    assert status == 502
    # The error message is generic and does not leak provider internals.
    assert "provider error" in payload["detail"].lower()
    assert "exploded" not in payload["detail"]


def test_provider_timeout_returns_504(monkeypatch):
    _register_analyzed("ask-timeout")

    class _Hang:
        def complete(self, **kwargs):
            # Simulate a provider that does not honor the timeout: sleep well
            # past the bound. asyncio.wait_for must cut it off.
            import time

            time.sleep(3)
            return "too late"

    monkeypatch.setattr(sessions, "get_provider", lambda: _Hang())
    # Shrink the bound so the test does not actually wait 30s; the sleep is
    # still far longer, so the wait_for timeout fires deterministically.
    monkeypatch.setattr(main, "ASK_TIMEOUT_SECONDS", 0.2)

    status, payload = _call_post_json(
        "/sessions/ask-timeout/ask", {"question": "What is my total?"}
    )
    assert status == 504
    assert "30 seconds" in payload["detail"]
