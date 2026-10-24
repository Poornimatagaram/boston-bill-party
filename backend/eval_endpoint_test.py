"""Verification test for POST /eval/run (task 16.2, Req 13).

Drives the ASGI app in-process (the same tiny client used by
``ask_endpoint_test.py`` / ``confirm_endpoint_test.py`` — no ``httpx`` is added)
and asserts the endpoint returns ``200`` with an EvalReport-shaped body.

No real LLM runs: the harness injects its own deterministic ``EvalLLMProvider``
for every pipeline run, so ``POST /eval/run`` is hermetic and consumes no API
credits. One test also patches ``main.run_evaluation`` to the empty-set error to
confirm the endpoint maps it to a ``400`` (Req 13.2).
"""

from __future__ import annotations

import json

import anyio
import pytest

import main
from eval.harness import EmptyEvalSetError


def _call_post(path: str) -> tuple[int, object]:
    """Send one POST request (no body) through the ASGI app; return (status, json)."""
    raw_body = b""
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", b"0"),
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


def test_eval_run_returns_report_shaped_body():
    """POST /eval/run returns 200 with an EvalReport-shaped body (Req 13.6)."""
    status, payload = _call_post("/eval/run")
    assert status == 200, payload

    # EvalReport shape: aggregate counts, failed-bill count, per-bill breakdown.
    assert set(payload) == {
        "detected",
        "missed",
        "false_positives",
        "failed_bills",
        "per_bill",
    }
    assert isinstance(payload["detected"], int)
    assert isinstance(payload["missed"], int)
    assert isinstance(payload["false_positives"], int)
    assert isinstance(payload["failed_bills"], int)
    assert isinstance(payload["per_bill"], list)

    # Ten bills, none failed (deterministic provider structures every bill), and
    # totals conserve per-bill counts.
    assert len(payload["per_bill"]) == 10
    assert payload["failed_bills"] == 0
    total_planted = sum(entry["planted"] for entry in payload["per_bill"])
    assert payload["detected"] + payload["missed"] == total_planted


def test_eval_run_empty_set_maps_to_400(monkeypatch):
    """Req 13.2: an empty Eval_Set surfaces as a 400 from the endpoint."""

    def _raise_empty():
        raise EmptyEvalSetError("The Eval_Set is empty; evaluation cannot run.")

    monkeypatch.setattr(main, "run_evaluation", _raise_empty)
    status, payload = _call_post("/eval/run")
    assert status == 400
    assert "empty" in payload["detail"].lower()
