"""Verification tests for GET /sessions/{id}/events SSE streaming (task 14.1).

No real LLM or pipeline runs: each test builds a :class:`sessions.Session` with a
hand-constructed :class:`PipelineState`, pre-populates its async event queue, and
registers it directly in the session registry, so the SSE endpoint is exercised
against known state. Requests are driven through the ASGI app with a tiny
in-process client — the SSE response streams, so the client collects
``http.response.body`` chunks until the stream signals completion (or the
terminating ``done`` frame appears), matching the approach in the other endpoint
tests (``main_test.py`` / ``result_endpoint_test.py``) which avoid the
``httpx``-backed ``TestClient``.

Covered (design "SSE Event Shapes", Req 9.2):
* unknown session id          -> 404 with a clear message
* pre-populated queue         -> named SSE frames: an initial ``progress`` snapshot,
                                 each queued ``progress`` frame, terminating ``done``
* node_error relay            -> ``event: node_error`` frame with {failedNode,message}
"""

from __future__ import annotations

import asyncio
import json

import anyio
import pytest

import main
import sessions
from models import SupportedLanguage
from pipeline.state import PipelineState
from sessions import Session, SessionStatus


def _call_sse(path: str) -> tuple[int, str]:
    """Drive one GET through the ASGI app, collecting the streamed body.

    Returns ``(status, body_text)``. For a streaming response we keep reading
    ``http.response.body`` messages until the server marks the final chunk
    (``more_body`` false) or a ``done`` SSE frame has been observed, so the test
    does not hang waiting on a long-lived stream.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }

    captured: dict = {"status": None, "body": bytearray(), "done": False}
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        # After the request body, report the client is still connected but idle;
        # the stream ends on its own after the done sentinel.
        await asyncio.sleep(0.01)
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
        elif message["type"] == "http.response.body":
            captured["body"] += message.get("body", b"")
            if b"event: done" in bytes(captured["body"]):
                captured["done"] = True

    async def drive():
        # Bound the run so a misbehaving stream can't hang the suite.
        with anyio.move_on_after(5):
            await main.app(scope, receive, send)

    anyio.run(drive)

    return captured["status"], bytes(captured["body"]).decode()


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into a list of (event_name, json_data) tuples."""
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        event_name = None
        data_str = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_str = line[len("data:"):].strip()
        if event_name is not None:
            events.append((event_name, json.loads(data_str) if data_str else {}))
    return events


def _register(state: PipelineState, status: SessionStatus) -> Session:
    session = Session(
        id=state.session_id, language=state.language, state=state, status=status
    )
    sessions._SESSIONS[state.session_id] = session
    return session


@pytest.fixture(autouse=True)
def _clean_registry():
    sessions.clear_sessions()
    yield
    sessions.clear_sessions()


def test_unknown_session_returns_404():
    status, body = _call_sse("/sessions/nope/events")
    assert status == 404
    payload = json.loads(body)
    assert "nope" in payload["detail"]


def test_streams_named_progress_frames_ending_with_done():
    state = PipelineState(session_id="sse-1", language=SupportedLanguage.EN)
    session = _register(state, SessionStatus.RUNNING)

    # Pre-populate the queue with two progress events and the terminal sentinel,
    # emulating node transitions already observed by the time the client connects.
    p1 = {
        "activeNode": "extractor",
        "completedNodes": [],
        "completedCount": 0,
        "total": 5,
    }
    p2 = {
        "activeNode": "auditor",
        "completedNodes": ["extractor"],
        "completedCount": 1,
        "total": 5,
    }
    session.queue.put_nowait(("progress", p1))
    session.queue.put_nowait(("progress", p2))
    session.queue.put_nowait(("done", None))

    status, body = _call_sse("/sessions/sse-1/events")
    assert status == 200, body

    events = _parse_sse(body)
    names = [name for name, _ in events]
    # First frame is the initial snapshot (progress), then the two queued
    # progress frames, then the terminating done.
    assert names[0] == "progress"
    assert names[-1] == "done"
    assert names.count("done") == 1
    # The two queued progress payloads are relayed verbatim.
    progress_payloads = [data for name, data in events if name == "progress"]
    assert p1 in progress_payloads
    assert p2 in progress_payloads
    # The done frame carries an empty object payload.
    assert events[-1] == ("done", {})


def test_relays_node_error_frame():
    state = PipelineState(session_id="sse-2", language=SupportedLanguage.EN)
    session = _register(state, SessionStatus.ERROR)

    err = {"failedNode": "writer", "message": "generation failed"}
    session.queue.put_nowait(("node_error", err))
    session.queue.put_nowait(("done", None))

    status, body = _call_sse("/sessions/sse-2/events")
    assert status == 200, body

    events = _parse_sse(body)
    assert ("node_error", err) in events
    assert events[-1][0] == "done"
