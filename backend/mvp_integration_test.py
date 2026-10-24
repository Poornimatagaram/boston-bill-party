"""End-to-end MVP integration test (task 11 — MVP milestone).

This test proves the whole slice wires together: a synthetic bill flows
``POST /sessions`` -> background Extractor -> Auditor -> Rights_Checker ->
Skeptic -> Writer pipeline ->
``GET /sessions/{id}/result`` (``completed``) -> ``POST /sessions/{id}/confirm``,
with the HTTP contract and field shapes the frontend reads
(``summaryEntries`` / ``appealLetter`` / ``retainedFindings`` / ``language``)
all exercised against the REAL app and REAL pipeline runner.

Hermetic by construction — NO network / NO real LLM:
  * The LLM provider is a deterministic in-memory stub (:class:`_StubProvider`).
    Its ``complete(...)`` returns canned JSON when called from the Extractor node
    and canned prose when called from the Writer node, routed by the ``node``
    argument every node passes. It is injected by monkeypatching
    ``sessions.get_provider`` so the real ``create_session`` -> background
    ``run_pipeline`` path (not a stubbed one) runs end to end.
  * ``pipeline.extractor.read_pdf_text`` is monkeypatched to return a fixed
    synthetic bill text. Rationale: the upload path's own corrupt-PDF guard
    (``main._assert_parseable_pdf``) requires a pdfplumber-openable document, but
    a hand-crafted minimal PDF yields NO extractable text — which would trip the
    Extractor's empty-text short-circuit (Req 2.7) before the stub provider is
    ever reached. Faking ``read_pdf_text`` makes the mock-provider path
    deterministic while still driving the genuine Extractor -> Auditor -> Writer
    logic (Sum_Check, duplicate/suspicious-code detection, per-finding summary).
    The uploaded bytes are a real minimal PDF so the upload validation (content
    type, size, parseability, synthetic marker) is exercised for real.

Everything runs on a single event loop inside one ``anyio.run`` coroutine so the
background pipeline task created by ``POST /sessions`` can be awaited to
completion before the result is polled. Requests are driven straight through the
ASGI app (Starlette's ``TestClient`` needs ``httpx``, which the project does not
add — same approach as the other endpoint tests).
"""

from __future__ import annotations

import json

import anyio
import pytest

import main
import sessions
import pipeline.extractor as extractor_mod

# A minimal, valid single-page PDF that pdfplumber can open. Reused from the
# upload-validation tests: it passes main's corrupt-PDF guard but yields no
# extractable text, which is exactly why read_pdf_text is faked below.
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
    b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000058 00000 n \n0000000115 00000 n \n"
    b"trailer<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF\n"
)

_BOUNDARY = "----bostonbillpartymvpboundary"

# Canned synthetic bill text the faked read_pdf_text returns. The numbers are
# chosen so the real Auditor produces findings deterministically:
#  * stated_total (250.00) != sum of amounts (100.00 + 50.00 + 50.00 = 200.00)
#    -> a math-error (bill-total mismatch) Finding (Req 3.2). This Finding
#    carries no line-item/passage refs, so the Skeptic (task 13.2) DROPS it from
#    the retained set (Req 6.3) — it is produced by the Auditor but never
#    reaches the Writer.
#  * two identical code+description lines (99213 / Office visit) -> a
#    duplicate-charge Finding grouping both (Req 4.2); it references its member
#    lines, so the Skeptic RETAINS it.
_SYNTHETIC_BILL_TEXT = (
    "GENERAL HOSPITAL — SYNTHETIC TEST BILL\n"
    "Account: SYN-0001\n"
    "99213 Office visit  qty 1  unit 100.00  amount 100.00\n"
    "99213 Office visit  qty 1  unit 50.00   amount 50.00\n"
    "85025 Blood panel   qty 1  unit 50.00   amount 50.00\n"
    "Total due: 250.00\n"
)

# Canned Extractor JSON the stub provider returns for the extractor node. Shaped
# to the ExtractedBill schema; the duplicate code+description pair and the
# total/line mismatch drive the Auditor's findings.
_EXTRACTOR_JSON = json.dumps(
    {
        "provider_name": "General Hospital",
        "provider_address": None,
        "account_number": "SYN-0001",
        "line_items": [
            {
                "id": "l1",
                "description": "Office visit",
                "billing_code": "99213",
                "quantity": 1,
                "unit_price": 100.00,
                "amount": 100.00,
            },
            {
                "id": "l2",
                "description": "Office visit",
                "billing_code": "99213",
                "quantity": 1,
                "unit_price": 50.00,
                "amount": 50.00,
            },
            {
                "id": "l3",
                "description": "Blood panel",
                "billing_code": "85025",
                "quantity": 1,
                "unit_price": 50.00,
                "amount": 50.00,
            },
        ],
        "stated_total": 250.00,
    }
)

# Canned Writer prose. The Writer phrases ONE summary sentence per finding (it
# calls the provider once per finding with node="writer") and one letter body,
# so a single canned string is reused for every writer call. The deterministic
# per-finding structure (Property 11) is enforced by the Writer itself.
_WRITER_SENTENCE = (
    "This charge appears to be a billing error you can contest."
)


class _StubProvider:
    """Deterministic in-memory LLM stand-in — no network, no credentials.

    Routes by the ``node`` argument every node passes to ``complete``:
      * ``"extractor"`` -> canned ExtractedBill JSON (:data:`_EXTRACTOR_JSON`)
      * ``"writer"``    -> canned prose (:data:`_WRITER_SENTENCE`)
    Any other node id raises, so an unexpected call surfaces loudly instead of
    silently returning something plausible.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, *, system: str, user: str, timeout_s: float, node) -> str:
        node_id = getattr(node, "value", node)
        self.calls.append(str(node_id))
        if node_id == "extractor":
            return _EXTRACTOR_JSON
        if node_id == "writer":
            return _WRITER_SENTENCE
        raise AssertionError(f"Unexpected provider call from node {node_id!r}")


def _encode_multipart(fields: dict[str, str], file_part: dict) -> bytes:
    """Build a minimal multipart/form-data body (mirrors main_test.py)."""
    out = bytearray()
    for name, value in fields.items():
        out += f"--{_BOUNDARY}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += value.encode() + b"\r\n"
    out += f"--{_BOUNDARY}\r\n".encode()
    out += (
        f'Content-Disposition: form-data; name="{file_part["name"]}"; '
        f'filename="{file_part["filename"]}"\r\n'
    ).encode()
    out += f'Content-Type: {file_part["content_type"]}\r\n\r\n'.encode()
    out += file_part["data"] + b"\r\n"
    out += f"--{_BOUNDARY}--\r\n".encode()
    return bytes(out)


async def _asgi_request(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, object]:
    """Drive one request through ``main.app`` on the CURRENT event loop.

    Unlike the ``anyio.run``-per-call helpers in the other test files, this is a
    coroutine so several requests (and the background pipeline task they spawn)
    share one loop — required so the task created by ``POST /sessions`` can be
    awaited before polling the result.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 8000),
    }

    captured: dict = {"status": None, "body": bytearray()}
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
        elif message["type"] == "http.response.body":
            captured["body"] += message.get("body", b"")

    await main.app(scope, receive, send)

    raw = bytes(captured["body"])
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        payload = raw.decode(errors="replace")
    return captured["status"], payload


@pytest.fixture(autouse=True)
def _mvp_env(monkeypatch):
    """Inject the stub provider and fake PDF text; keep the real pipeline.

    Crucially we do NOT neutralize ``run_pipeline`` (unlike main_test.py): the
    genuine Extractor -> Auditor -> Writer runner executes, driven only by the
    deterministic stub provider and canned bill text.
    """
    stub = _StubProvider()
    monkeypatch.setattr(sessions, "get_provider", lambda: stub)
    monkeypatch.setattr(
        extractor_mod, "read_pdf_text", lambda _source: _SYNTHETIC_BILL_TEXT
    )
    sessions.clear_sessions()
    yield stub
    sessions.clear_sessions()


async def _create_session() -> tuple[int, object]:
    body = _encode_multipart(
        {"language": "en", "synthetic": "true"},
        {
            "name": "pdf",
            "filename": "synthetic-bill.pdf",
            "content_type": "application/pdf",
            "data": _MINIMAL_PDF,
        },
    )
    headers = [
        (b"content-type", f"multipart/form-data; boundary={_BOUNDARY}".encode()),
        (b"content-length", str(len(body)).encode()),
    ]
    return await _asgi_request("POST", "/sessions", body=body, headers=headers)


async def _poll_until_terminal(session_id: str, *, max_polls: int = 50) -> dict:
    """Poll GET result until status is ``completed`` or ``error`` (or give up)."""
    path = f"/sessions/{session_id}/result"
    last: dict = {}
    for _ in range(max_polls):
        status, payload = await _asgi_request("GET", path)
        assert status == 200, payload
        assert isinstance(payload, dict)
        last = payload
        if payload["status"] in ("completed", "error"):
            return payload
        # Still running: let the background pipeline task make progress.
        await anyio.sleep(0.02)
    return last


def test_mvp_end_to_end_upload_result_confirm(_mvp_env):
    """Upload -> pipeline -> completed result -> confirm, all in one loop."""
    stub = _mvp_env

    async def scenario():
        # 1. Upload the synthetic bill (POST /sessions). The handler validates
        #    the real minimal PDF, accepts the synthetic marker, and launches the
        #    background pipeline, returning a sessionId fast (Req 1.1, 1.7, 12.2).
        status, payload = await _create_session()
        assert status == 200, payload
        assert isinstance(payload, dict)
        session_id = payload["sessionId"]
        assert session_id

        # 2. Drive the background pipeline to completion. Awaiting the session's
        #    task is the deterministic way to know the Extractor -> Auditor ->
        #    Rights_Checker -> Skeptic -> Writer chain has finished before we
        #    read the result.
        session = sessions.get_session(session_id)
        assert session is not None and session.task is not None
        await session.task

        # 3. Poll GET /sessions/{id}/result until it reports a terminal status
        #    (the frontend's Progress screen polls this same endpoint).
        result = await _poll_until_terminal(session_id)
        assert result["status"] == "completed", result

        # 4. The completed body carries exactly the camelCase fields the frontend
        #    reads (summaryEntries / appealLetter / retainedFindings / language).
        assert result["language"] == "en"
        summary = result["summaryEntries"]
        findings = result["retainedFindings"]
        assert isinstance(summary, list) and summary, result
        assert isinstance(findings, list) and findings, result
        # Property 11 wiring: one summary entry per retained finding.
        assert len(summary) == len(findings)

        # Post-Skeptic retained set (task 13.2): the Skeptic drops any Finding
        # that lacks a line-item OR passage reference, or whose confidence is
        # below 0.5 (Req 6.2, 6.3). The Auditor's bill-total mismatch math_error
        # carries BOTH totals but NO line_item_ids and NO passage_ids, so it is
        # correctly DROPPED by the Skeptic and does NOT appear in the retained
        # set. The duplicate-charge Finding references its two member lines, so
        # it is RETAINED. (Exact types proves Extractor JSON -> Auditor logic ran
        # and that the Skeptic filtered on evidence, not that "some" findings
        # appeared.)
        types = {f["type"] for f in findings}
        assert "duplicate_charge" in types, types
        # The ref-less bill-total math_error is filtered out by the Skeptic.
        assert "math_error" not in types, types

        # Every retained Finding is evidenced (>=1 line item OR >=1 passage) and
        # confident (>=0.5) — the Skeptic invariant it just enforced (Req 6.4).
        for finding in findings:
            has_ref = bool(finding["line_item_ids"]) or bool(
                finding["passage_ids"]
            )
            assert has_ref, finding
            assert float(finding["confidence"]) >= 0.5, finding

        # The duplicate-charge Finding references BOTH office-visit lines.
        dup = next(f for f in findings if f["type"] == "duplicate_charge")
        assert set(dup["line_item_ids"]) == {"l1", "l2"}, dup

        # With >=1 finding the Writer produced an editable appeal letter (Req 7.2).
        assert isinstance(result["appealLetter"], str)
        assert result["appealLetter"].strip()

        # The stub provider was actually exercised through both nodes — proving
        # the Extractor and Writer really called the (mocked) LLM path.
        assert "extractor" in stub.calls
        assert "writer" in stub.calls

        # 5. Confirm the (edited) letter (POST /sessions/{id}/confirm). This is
        #    the explicit confirm gate; the backend records EXACTLY this text
        #    (Req 8.5-8.7), not the Writer's draft.
        edited = result["appealLetter"] + "\n\n[Edited by the patient before sending.]"
        body = json.dumps({"letter": edited}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]
        status, payload = await _asgi_request(
            "POST", f"/sessions/{session_id}/confirm", body=body, headers=headers
        )
        assert status == 200, payload
        assert isinstance(payload, dict)
        assert payload["status"] == "confirmed"
        assert payload["length"] == len(edited)

        # The session holds the exact edited text the client confirmed.
        confirmed_session = sessions.get_session(session_id)
        assert confirmed_session is not None
        assert confirmed_session.confirmed_letter == edited

    anyio.run(scenario)
