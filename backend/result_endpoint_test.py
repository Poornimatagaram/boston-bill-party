"""Verification tests for GET /sessions/{id}/result (task 8.2).

No real LLM or pipeline runs: each test builds a :class:`sessions.Session` with a
hand-constructed :class:`PipelineState` and registers it directly in the session
registry, so the endpoint's behavior is exercised against known state. Requests
are driven through the ASGI app with a tiny in-process client (the same approach
as ``main_test.py`` — Starlette's ``TestClient`` needs ``httpx``, which this task
does not add).

Covered (design "HTTP API", Req 7.1, 8.1; writer-retry path Req 8.2):
* unknown session id          -> 404 with a clear message
* running session             -> 200 {status: "running", progress: {...}}
* completed session           -> 200 {status: "completed", summaryEntries,
                                 appealLetter, retainedFindings, language} with
                                 Finding Decimal/enum fields serialized correctly
* node-fatal error (no result)-> 200 {status: "error", error: {failedNode,message}}
* node-fatal error + partial  -> 200 {status: "error", error, summaryEntries...}
"""

from __future__ import annotations

import json
from decimal import Decimal

import anyio
import pytest

import main
import sessions
from models import (
    AnalysisResult,
    Finding,
    FindingType,
    SupportedLanguage,
)
from pipeline.state import PipelineState
from public_errors import WRITER_MESSAGE
from sessions import Session, SessionStatus


def _call_get(path: str):
    """Send one GET request through the ASGI app; return (status, json)."""
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

    captured: dict = {"status": None, "body": bytearray()}
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
        elif message["type"] == "http.response.body":
            captured["body"] += message.get("body", b"")

    async def drive():
        await main.app(scope, receive, send)

    anyio.run(drive)

    raw = bytes(captured["body"])
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        payload = raw.decode(errors="replace")
    return captured["status"], payload


def _register(state: PipelineState, status: SessionStatus) -> str:
    """Build a Session around a hand-made state, register it, return the id."""
    session = Session(
        id=state.session_id, language=state.language, state=state, status=status
    )
    sessions._SESSIONS[state.session_id] = session
    return state.session_id


@pytest.fixture(autouse=True)
def _clean_registry():
    sessions.clear_sessions()
    yield
    sessions.clear_sessions()


def test_unknown_session_returns_404():
    status, payload = _call_get("/sessions/does-not-exist/result")
    assert status == 404
    assert "does-not-exist" in payload["detail"]


def test_running_session_returns_not_ready_with_progress():
    state = PipelineState(session_id="run-1", language=SupportedLanguage.EN)
    _register(state, SessionStatus.RUNNING)

    status, payload = _call_get("/sessions/run-1/result")
    assert status == 200, payload
    assert payload["status"] == "running"
    # Progress mirrors the SSE picture with the five-node denominator.
    assert payload["progress"]["total"] == 5
    assert payload["progress"]["completedCount"] == 0
    assert "summaryEntries" not in payload


def test_completed_session_returns_serialized_result():
    finding = Finding(
        id="f1",
        type=FindingType.MATH_ERROR,
        description="Bill total does not match line items.",
        confidence=Decimal("0.9"),
        line_item_ids=["l1"],
        computed_total=Decimal("100.5"),
        stated_total=Decimal("120"),
    )
    result = AnalysisResult(
        summary_entries=["The bill total is overstated."],
        appeal_letter="Dear provider, I contest the following charge...",
        retained_findings=[finding],
        language=SupportedLanguage.ES,
    )
    state = PipelineState(session_id="done-1", language=SupportedLanguage.ES)
    state.result = result
    _register(state, SessionStatus.COMPLETED)

    status, payload = _call_get("/sessions/done-1/result")
    assert status == 200, payload
    assert payload["status"] == "completed"
    assert payload["summaryEntries"] == ["The bill total is overstated."]
    assert payload["appealLetter"].startswith("Dear provider")
    assert payload["language"] == "es"

    # Finding Decimal/enum fields serialize correctly (2dp strings, enum values).
    retained = payload["retainedFindings"]
    assert len(retained) == 1
    rf = retained[0]
    assert rf["type"] == "math_error"
    assert rf["computed_total"] == "100.50"
    assert rf["stated_total"] == "120.00"
    assert rf["line_item_ids"] == ["l1"]


def test_completed_no_findings_has_null_appeal_letter():
    result = AnalysisResult(
        summary_entries=["No credible billing errors or rights issues were detected."],
        appeal_letter=None,
        retained_findings=[],
        language=SupportedLanguage.EN,
    )
    state = PipelineState(session_id="none-1", language=SupportedLanguage.EN)
    state.result = result
    _register(state, SessionStatus.COMPLETED)

    status, payload = _call_get("/sessions/none-1/result")
    assert status == 200, payload
    assert payload["status"] == "completed"
    assert payload["appealLetter"] is None
    assert payload["retainedFindings"] == []


def test_error_session_returns_error_shape_without_result():
    state = PipelineState(session_id="err-1", language=SupportedLanguage.EN)
    state.error = {"failedNode": "writer", "message": "generation failed"}
    _register(state, SessionStatus.ERROR)

    status, payload = _call_get("/sessions/err-1/result")
    assert status == 200, payload
    assert payload["status"] == "error"
    # The internal detail is replaced by the fixed friendly message.
    assert payload["error"] == {"failedNode": "writer", "message": WRITER_MESSAGE}
    # Writer is node-fatal with no partial output: no summary present.
    assert "summaryEntries" not in payload


def test_error_session_includes_existing_summary_for_retry():
    # An error at a later retryable point should still surface any existing
    # Summary so the writer-retry path (Req 8.2) has it.
    result = AnalysisResult(
        summary_entries=["The bill total is overstated."],
        appeal_letter="Draft letter text.",
        retained_findings=[],
        language=SupportedLanguage.PT,
    )
    state = PipelineState(session_id="err-2", language=SupportedLanguage.PT)
    state.result = result
    state.error = {"failedNode": "writer", "message": "retry point"}
    _register(state, SessionStatus.ERROR)

    status, payload = _call_get("/sessions/err-2/result")
    assert status == 200, payload
    assert payload["status"] == "error"
    assert payload["error"]["failedNode"] == "writer"
    # The existing Summary rides along for the retry flow.
    assert payload["summaryEntries"] == ["The bill total is overstated."]
    assert payload["appealLetter"] == "Draft letter text."
    assert payload["language"] == "pt"
