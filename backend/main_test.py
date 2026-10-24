"""Verification tests for POST /sessions upload validation (task 8.1).

These tests exercise the ordered upload validation and fast session creation in
:mod:`main`. No real LLM is involved: the LLM provider is mocked and the
background pipeline runner is replaced with a harmless no-op, so the handler's
``create_session`` launch starts a background task that never makes a network
call.

Starlette's ``TestClient`` needs ``httpx`` (not installed, and this task adds no
dependencies), so instead we drive the ASGI app directly through the ASGI
protocol with a tiny in-process client built on ``anyio`` (already present via
uvicorn). A small multipart encoder builds the ``multipart/form-data`` body the
handler's ``UploadFile``/``Form`` fields expect.

Covered (design "Edge Cases and Validation", Req 1.1–1.7, 12.2):
* non-PDF content type        -> 400 with a PDF-format message (Req 1.2)
* 0 bytes                      -> 400 size-range message (Req 1.3)
* > 10 MB                      -> 400 size-range message (Req 1.3)
* PDF content type, corrupt    -> 400 corrupt/unreadable message (Req 1.6)
* missing synthetic marker     -> 400 non-synthetic error (Req 12.2)
* valid small synthetic PDF    -> 200 with a sessionId, returned promptly
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import anyio
import pytest

import main
import sessions

# A minimal, valid single-page PDF that pdfplumber can open (happy path).
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n"
    b"2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
    b"0000000058 00000 n \n0000000115 00000 n \n"
    b"trailer<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF\n"
)

_BOUNDARY = "----bostonbillpartytestboundary"


def _encode_multipart(fields: dict[str, str], file_part: dict) -> bytes:
    """Build a minimal multipart/form-data body.

    ``fields`` are simple name->value text parts; ``file_part`` is
    ``{name, filename, content_type, data}`` for the single file field.
    """
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


def _call_asgi(body: bytes, extra_headers: list[tuple[bytes, bytes]]):
    """Send one POST /sessions request through the ASGI app; return (status, json)."""
    headers = [
        (b"content-type", f"multipart/form-data; boundary={_BOUNDARY}".encode()),
        (b"content-length", str(len(body)).encode()),
        *extra_headers,
    ]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/sessions",
        "raw_path": b"/sessions",
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
            return {"type": "http.request", "body": body, "more_body": False}
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


@pytest.fixture(autouse=True)
def _mock_pipeline(monkeypatch):
    """Mock the provider and neutralize the background pipeline runner.

    No real config/network is touched: ``get_provider`` returns a MagicMock and
    ``run_pipeline`` is a no-op, so the background task the handler launches
    cannot make an LLM call.
    """
    monkeypatch.setattr(sessions, "get_provider", lambda: MagicMock())
    monkeypatch.setattr(sessions, "run_pipeline", lambda *a, **k: None)
    sessions.clear_sessions()
    yield
    sessions.clear_sessions()


def _upload(*, data, filename, content_type, language="en", synthetic=True, header=None):
    fields = {"language": language}
    if synthetic:
        fields["synthetic"] = "true"
    body = _encode_multipart(
        fields,
        {"name": "pdf", "filename": filename, "content_type": content_type, "data": data},
    )
    extra = [header] if header else []
    return _call_asgi(body, extra)


def test_non_pdf_rejected_with_pdf_message():
    status, payload = _upload(
        data=b"hello", filename="note.txt", content_type="text/plain"
    )
    assert status == 400
    assert "PDF" in payload["detail"]


def test_zero_bytes_rejected_with_size_message():
    status, payload = _upload(
        data=b"", filename="empty.pdf", content_type="application/pdf"
    )
    assert status == 400
    assert "size range" in payload["detail"].lower()


def test_over_10mb_rejected_with_size_message():
    too_big = b"%PDF-1.4\n" + b"0" * (main.MAX_UPLOAD_BYTES + 1)
    status, payload = _upload(
        data=too_big, filename="big.pdf", content_type="application/pdf"
    )
    assert status == 400
    assert "size range" in payload["detail"].lower()


def test_corrupt_pdf_rejected():
    status, payload = _upload(
        data=b"%PDF-1.4 this is not really a pdf",
        filename="corrupt.pdf",
        content_type="application/pdf",
    )
    assert status == 400
    assert "corrupt" in payload["detail"].lower()


def test_missing_synthetic_marker_rejected():
    status, payload = _upload(
        data=_MINIMAL_PDF,
        filename="bill.pdf",
        content_type="application/pdf",
        synthetic=False,
    )
    assert status == 400
    assert "synthetic" in payload["detail"].lower()


def test_valid_synthetic_pdf_accepted_fast():
    start = time.monotonic()
    status, payload = _upload(
        data=_MINIMAL_PDF, filename="bill.pdf", content_type="application/pdf"
    )
    elapsed = time.monotonic() - start
    assert status == 200, payload
    assert payload.get("sessionId")
    # Fast session creation (Req 1.7): generously under the 5s budget.
    assert elapsed < 5.0


def test_synthetic_marker_via_header():
    status, payload = _upload(
        data=_MINIMAL_PDF,
        filename="bill.pdf",
        content_type="application/pdf",
        synthetic=False,
        header=(b"x-synthetic-data", b"true"),
    )
    assert status == 200, payload
    assert payload.get("sessionId")


def test_unsupported_language_rejected():
    status, payload = _upload(
        data=_MINIMAL_PDF,
        filename="bill.pdf",
        content_type="application/pdf",
        language="fr",
    )
    assert status == 400
    assert "language" in payload["detail"].lower()
