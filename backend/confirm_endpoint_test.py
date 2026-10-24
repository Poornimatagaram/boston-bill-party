"""Verification tests for POST /sessions/{id}/confirm (task 10.3).

No real LLM or pipeline runs: each test builds a :class:`sessions.Session` with a
hand-constructed :class:`PipelineState` and registers it directly in the session
registry, so the confirm gate is exercised against known state. Requests are
driven through the ASGI app with a tiny in-process client (the same approach as
``main_test.py`` / ``result_endpoint_test.py`` — Starlette's ``TestClient`` needs
``httpx``, which these tasks do not add).

Covered (design "HTTP API", explicit confirm gate Req 8.5–8.7; letter bound Req
8.3):
* unknown session id                 -> 404, nothing recorded
* valid confirm with a letter        -> 200 {status:"confirmed", length} and the
                                        session records EXACTLY that text (the
                                        backend does not substitute the original)
* letter > 20,000 characters         -> 400, nothing recorded
* letter at exactly 20,000 chars     -> 200 (inclusive bound)
"""

from __future__ import annotations

import json

import anyio
import pytest

import main
import sessions
from main import MAX_LETTER_CHARS
from models import SupportedLanguage
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


def _register(session_id: str) -> Session:
    """Build a RUNNING session around a fresh state, register it, return it."""
    state = PipelineState(session_id=session_id, language=SupportedLanguage.EN)
    session = Session(
        id=session_id, language=SupportedLanguage.EN, state=state,
        status=SessionStatus.RUNNING,
    )
    sessions._SESSIONS[session_id] = session
    return session


@pytest.fixture(autouse=True)
def _clean_registry():
    sessions.clear_sessions()
    yield
    sessions.clear_sessions()


def test_unknown_session_returns_404():
    status, payload = _call_post_json(
        "/sessions/does-not-exist/confirm", {"letter": "anything"}
    )
    assert status == 404
    assert "does-not-exist" in payload["detail"]


def test_valid_confirm_records_exact_text():
    session = _register("confirm-1")
    edited = "Dear provider, I contest charge #42 (EDITED BY USER)."

    status, payload = _call_post_json(
        "/sessions/confirm-1/confirm", {"letter": edited}
    )
    assert status == 200, payload
    assert payload["status"] == "confirmed"
    assert payload["length"] == len(edited)
    # The session records EXACTLY the edited text received (Req 8.6/8.7): the
    # backend does not substitute the original draft.
    assert session.confirmed_letter == edited


def test_empty_letter_is_accepted():
    session = _register("confirm-empty")
    status, payload = _call_post_json(
        "/sessions/confirm-empty/confirm", {"letter": ""}
    )
    assert status == 200, payload
    assert payload["length"] == 0
    assert session.confirmed_letter == ""


def test_letter_at_max_length_is_accepted():
    session = _register("confirm-max")
    at_bound = "x" * MAX_LETTER_CHARS
    status, payload = _call_post_json(
        "/sessions/confirm-max/confirm", {"letter": at_bound}
    )
    assert status == 200, payload
    assert payload["length"] == MAX_LETTER_CHARS
    assert session.confirmed_letter == at_bound


def test_letter_over_max_length_rejected_without_recording():
    session = _register("confirm-toolong")
    too_long = "y" * (MAX_LETTER_CHARS + 1)
    status, payload = _call_post_json(
        "/sessions/confirm-toolong/confirm", {"letter": too_long}
    )
    assert status == 400
    assert str(MAX_LETTER_CHARS) in payload["detail"]
    # Nothing recorded on a rejected confirm.
    assert session.confirmed_letter is None
