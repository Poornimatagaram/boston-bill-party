"""FastAPI application entrypoint, HTTP routes, and SSE streaming.

This module hosts the Boston Bill Party backend HTTP surface. Task 8.1
implements ``POST /sessions`` (upload validation + fast session creation).
Later tasks (8.2, 10, 14, 15) add the result, confirm, events (SSE), and ask
endpoints — the module is structured so those routes slot in beside the one
implemented here without reshaping the app.

Upload validation order (design "HTTP API", Req 1.1–1.7, 12.2). Each failing
check returns ``400`` with a clear message and creates NO session:

1. Non-PDF content type -> message naming PDF as the accepted format (Req 1.2).
2. Size outside ``[1 byte, 10 MB]`` -> size-range message (Req 1.3).
3. PDF content type but unparseable -> corrupt/unreadable message (Req 1.6).
4. Input not marked synthetic -> non-synthetic error (Req 12.2).
5. Unsupported ``language`` value -> clear message (Req 1.4 context).

On acceptance the handler starts a background pipeline via
:func:`sessions.create_session` and returns ``{"sessionId": ...}`` immediately
(Req 1.1, 1.7) — it never awaits the pipeline.

Synthetic-data marker (Req 12.1/12.2)
-------------------------------------
The system operates exclusively on synthetic data, so every upload must carry
an explicit "this is synthetic" signal. The accepted, documented mechanism is:

* a multipart form field ``synthetic`` whose value is a truthy string
  (``"true"``, ``"1"``, ``"yes"``, or ``"on"``, case-insensitive), OR
* an ``X-Synthetic-Data: true`` request header (same truthy set).

If neither marker is present/true, the upload is rejected with a non-synthetic
error and no session is created. The frontend contract (``frontend/src/lib/
api.ts``) will need to append the ``synthetic`` form field; this task does not
modify the frontend.

Security note: this module never reads, logs, or prints the LLM API key. Config
is validated at startup (Req 11.2/11.3) via a lifespan hook, but a config error
is surfaced without leaking the key (see :mod:`config`).
"""

from __future__ import annotations

import asyncio
import io
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

import pdfplumber
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import rate_limit
import sessions
from public_errors import logger, public_error
from config import (
    ConfigError,
    NodeName,
    load_allowed_origins,
    load_config,
    load_max_daily_analyses,
)
from eval.harness import EmptyEvalSetError, run_evaluation
from models import SupportedLanguage
from pipeline.writer import translate_letter
from sessions import create_session, get_session

# --- Upload size bounds (Req 1.3) -------------------------------------------
# Inclusive range: 1 byte minimum (0 bytes rejected), 10 MB maximum
# (10 MB + 1 byte rejected). 10 MB is 10 * 1024 * 1024 bytes.
MIN_UPLOAD_BYTES = 1
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# --- Confirm letter bound (Req 8.3) -----------------------------------------
# The editable appeal-letter field accepts up to 20,000 characters on the UI;
# the confirm endpoint enforces the same upper bound on the text it records so
# the confirmed (transmitted) letter respects the field's documented limit.
MAX_LETTER_CHARS = 20_000

# --- Ask question bounds (Req 10.3) -----------------------------------------
# A Voice_QA transcript is submitted only when it is 1–500 characters
# (Req 10.3). The frontend enforces this, but the backend validates too so a
# direct/misbehaving client cannot submit an empty or oversized question.
MIN_QUESTION_CHARS = 1
MAX_QUESTION_CHARS = 500

# --- Ask answer timeout (Req 10.6, 10.7) ------------------------------------
# The backend must return an answer within 30 seconds or a timeout error
# (Req 10.7). We bound the (blocking) provider call at 30s and surface a clear
# timeout error if it is exceeded.
ASK_TIMEOUT_SECONDS = 30.0

# --- Letter translation timeout ---------------------------------------------
# Translating a full letter is longer prose generation than a Q&A answer, so it
# gets a wider bound. A slow provider yields a clear 504 instead of hanging.
TRANSLATE_TIMEOUT_SECONDS = 60.0

# Truthy string values accepted for the synthetic-data marker (Req 12.2).
_TRUTHY = {"true", "1", "yes", "on"}

# Origins permitted by CORS: ALLOWED_ORIGINS (comma-separated) from the
# environment or backend/.env, defaulting to the local Next.js dev server.
ALLOWED_ORIGINS = load_allowed_origins()


def _is_pdf_upload(upload: UploadFile) -> bool:
    """Return whether the upload looks like a PDF (Req 1.2).

    Prefers the declared ``content_type`` (``application/pdf``) and falls back
    to a ``.pdf`` filename extension when the content type is missing or
    generic (browsers and clients sometimes send ``application/octet-stream``).
    """
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type == "application/pdf":
        return True
    # Content-type fallback: accept only when the declared type is absent or a
    # generic binary type AND the filename clearly ends in .pdf.
    if content_type in ("", "application/octet-stream"):
        filename = (upload.filename or "").lower()
        return filename.endswith(".pdf")
    return False


def _is_marked_synthetic(
    synthetic_field: Optional[str], synthetic_header: Optional[str]
) -> bool:
    """Return whether the input is explicitly marked synthetic (Req 12.2).

    True when either the ``synthetic`` form field or the ``X-Synthetic-Data``
    header holds a truthy string. Absent/false markers return False so the
    upload is rejected without processing.
    """
    for value in (synthetic_field, synthetic_header):
        if value is not None and value.strip().lower() in _TRUTHY:
            return True
    return False


def _parse_language(language: str) -> SupportedLanguage:
    """Validate and coerce the ``language`` form field (Req 1.4).

    Raises ``HTTPException(400)`` with a clear message listing the supported
    language codes when the value is not a :class:`SupportedLanguage`.
    """
    try:
        return SupportedLanguage(language.strip().lower())
    except ValueError:
        supported = ", ".join(lang.value for lang in SupportedLanguage)
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported language {language!r}. "
                f"Supported languages: {supported}."
            ),
        )


def _assert_parseable_pdf(data: bytes) -> None:
    """Reject an unparseable PDF as corrupt/unreadable (Req 1.6).

    This is a cheap open/validation (confirm pdfplumber can open the bytes),
    not the full text extraction the Extractor node performs later. Any failure
    opening the document is treated as a corrupt/unreadable file.
    """
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            # Touch the page list so a lazily-validated structure actually
            # raises here rather than deep in the pipeline.
            _ = pdf.pages
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="The uploaded PDF is corrupt or unreadable.",
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate LLM provider config at startup (Req 11.2/11.3).

    A misconfigured deployment aborts here with a clear error (the message
    names offending variables but never the API key value). Importing this
    module stays cheap — validation runs only when the app actually starts, so
    unit tests that construct a ``TestClient`` without a valid ``.env`` can
    still exercise upload validation by catching the startup error, or by
    running against an app whose dependencies are mocked.
    """
    try:
        load_config()
        # Fail fast on a malformed cost-protection cap.
        load_max_daily_analyses()
    except ConfigError:
        # Re-raise so a real deployment aborts startup (Req 11.2/11.3). The
        # ConfigError message already names the offending variables and never
        # contains the API key value.
        raise
    yield


app = FastAPI(title="Boston Bill Party", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    """Liveness check for the frontend's cold-start ping.

    Cheap by design: no LLM call, no knowledge-base build, no config details.
    """
    return {"status": "ok"}


@app.post("/sessions")
async def create_session_endpoint(
    request: Request,
    pdf: UploadFile = File(...),
    language: str = Form(...),
    synthetic: Optional[str] = Form(default=None),
    x_synthetic_data: Optional[str] = Header(default=None),
):
    """Accept a synthetic medical bill PDF and start an analysis session.

    Validates the upload in the documented order (Req 1.2 -> 1.3 -> 1.6 ->
    12.2 -> language), creating NO session on any rejection. On acceptance it
    launches the pipeline in the background and returns the new session id
    immediately so the response is well within 5s (Req 1.1, 1.7).
    """
    # 1. Content type (Req 1.2) — reject non-PDF before reading any bytes.
    if not _is_pdf_upload(pdf):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file type. The accepted format is PDF.",
        )

    # Read the bytes once so we can size-check and validate without a path.
    data = await pdf.read()

    # 2. Size bounds (Req 1.3) — inclusive [1 byte, 10 MB].
    size = len(data)
    if size < MIN_UPLOAD_BYTES or size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                "File size out of range. The accepted size range is "
                "1 byte to 10 megabytes."
            ),
        )

    # 3. Parseable PDF (Req 1.6) — cheap open check, reject corrupt files.
    _assert_parseable_pdf(data)

    # 4. Synthetic marker (Req 12.2) — the system processes synthetic data only.
    if not _is_marked_synthetic(synthetic, x_synthetic_data):
        raise HTTPException(
            status_code=400,
            detail=(
                "Input is not marked as synthetic. This system processes only "
                "synthetic data; mark the upload synthetic (form field "
                "'synthetic=true' or header 'X-Synthetic-Data: true')."
            ),
        )

    # 5. Language (Req 1.4) — validate against SupportedLanguage.
    selected_language = _parse_language(language)

    # 6. Cost protection: per-IP hourly limit and global daily cap. Checked
    # last so rejected uploads never use up a visitor's quota.
    if not rate_limit.limiter.allow_analysis(rate_limit.client_ip(request)):
        raise rate_limit.demo_limit_error()

    # Accepted: start the session (background pipeline) and return fast (Req 1.7).
    session_id = create_session(io.BytesIO(data), selected_language)
    return {"sessionId": session_id}


def _serialize_result(result) -> dict:
    """Serialize an ``AnalysisResult`` into the camelCase shape the frontend reads.

    The frontend (``frontend/src/lib/api.ts``) normalizes both ``summary``/
    ``summaryEntries`` and ``findings``/``retainedFindings`` and reads
    ``appealLetter``, so we return the canonical camelCase keys directly. We use
    the pydantic model's JSON serialization (``model_dump(mode="json")``) so the
    model's ``field_serializer`` hooks run: ``Decimal`` monetary fields on each
    Finding render as two-decimal strings and the ``FindingType`` enum renders as
    its string value (design "data models", Req 3.2/4.6 serialization).
    """
    payload = result.model_dump(mode="json")
    return {
        "summaryEntries": payload["summary_entries"],
        "appealLetter": payload["appeal_letter"],
        "retainedFindings": payload["retained_findings"],
        "language": payload["language"],
    }


@app.get("/sessions/{session_id}/result")
async def get_session_result(session_id: str):
    """Return the analysis result for a session (Req 7.1, 8.1).

    Status-code choice: this endpoint always responds ``200`` for a *known*
    session and distinguishes outcomes with a top-level ``status`` field, so the
    client's handling stays uniform and a not-ready response is never confused
    with a completed one. Only an unknown session id is an HTTP error (``404``).

    Response shapes, keyed by ``status``:

    * ``"running"`` — the pipeline has not finished and no node-fatal error has
      occurred yet. Body: ``{status, progress}`` where ``progress`` mirrors the
      live ``{activeNode, completedNodes, completedCount, total}`` picture so the
      client can keep polling (it normally learns completion via SSE, but this is
      an unambiguous fallback). Chosen over 202/409 so the client parses one
      consistent JSON body for every known-session case.
    * ``"completed"`` — the Writer produced a result. Body: ``{status,
      summaryEntries, appealLetter, retainedFindings, language}`` with Finding
      monetary/enum fields serialized via the pydantic models (two-decimal
      strings, enum values).
    * ``"error"`` — a node-fatal error is recorded in ``state.error``. Body:
      ``{status, error: {failedNode, message}}`` PLUS ``summaryEntries`` /
      ``appealLetter`` / ``retainedFindings`` / ``language`` IF a partial result
      already exists on the state. This supports the writer-retry path (Req 8.2):
      the frontend keeps any Summary it already has while offering retry. Note the
      Writer is node-fatal with NO partial output (Req 7.5), so on a Writer
      failure there is typically no result and only the ``error`` is returned; an
      error at a later retryable point still surfaces an existing Summary.

    Args:
        session_id: The session UUID from the path.

    Raises:
        HTTPException: ``404`` when the session id is unknown.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"No session found for id {session_id!r}.",
        )

    # Node-fatal error takes precedence: surface the error, and include any
    # Summary already produced so the writer-retry path (Req 8.2) can function.
    if session.error is not None:
        # Clients get a fixed friendly message; the detail is in the server log.
        body: dict = {"status": "error", "error": public_error(session.error)}
        if session.result is not None:
            body.update(_serialize_result(session.result))
        return body

    # Completed successfully: the Writer produced an AnalysisResult.
    if session.result is not None:
        return {"status": "completed", **_serialize_result(session.result)}

    # Not ready yet: still running, no result and no error. Return the live
    # progress picture so the client can distinguish this from a completed result.
    return {"status": "running", "progress": session.progress}


# --- SSE framing (task 14.1) ------------------------------------------------
# text/event-stream frames are ``event: <name>\n`` followed by one or more
# ``data: <line>\n`` lines and a blank line terminator. We emit a single
# JSON-encoded data line per event. Headers discourage buffering so the client
# (frontend/src/lib/api.ts EventSource) receives each frame promptly.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Tell reverse proxies (e.g. nginx) not to buffer the stream.
    "X-Accel-Buffering": "no",
}


def _sse_frame(event: str, data: Optional[dict]) -> str:
    """Format one named SSE frame: ``event: <name>\\ndata: <json>\\n\\n``.

    ``data`` is JSON-encoded (``{}`` for the terminal ``done`` event, which
    carries no payload). The frontend's ``EventSource`` listeners parse the
    JSON ``data`` for each named event (``progress``/``node_error``/``done``).
    """
    payload = json.dumps(data if data is not None else {})
    return f"event: {event}\ndata: {payload}\n\n"


async def _session_event_stream(session) -> AsyncIterator[str]:
    """Yield SSE frames by draining the session's async event queue (Req 9.2).

    Emits an initial ``progress`` snapshot from ``session.progress`` so a late
    subscriber immediately sees the current picture, then relays every queued
    ``(event_type, data)`` tuple as a named frame. The queue carries one
    ``progress`` event per node RUNNING/COMPLETED transition (produced in
    ``sessions.py``), so faithfully relaying it yields one update per node
    start/completion across all five nodes. Streaming stops after the terminal
    ``("done", None)`` sentinel.

    Client disconnect: ``StreamingResponse`` cancels this generator when the
    client goes away, surfacing as :class:`asyncio.CancelledError` at the
    ``await queue.get()`` point; we stop quietly rather than erroring.
    """
    # Initial snapshot so a late/just-connected subscriber has current state
    # without waiting for the next transition (the queue may also already hold
    # the full history, including done, for an already-finished pipeline).
    try:
        yield _sse_frame("progress", session.progress)
        while True:
            event_type, data = await session.queue.get()
            if event_type == "done":
                yield _sse_frame("done", None)
                break
            yield _sse_frame(event_type, data)
    except asyncio.CancelledError:  # client disconnected — stop streaming
        raise


@app.get("/sessions/{session_id}/events")
async def stream_session_events(session_id: str):
    """Stream live pipeline progress for a session over SSE (Req 9.2).

    Returns a ``text/event-stream`` :class:`StreamingResponse` that relays the
    session's async event queue as named SSE events:

    * ``progress``   — ``{activeNode, completedNodes, completedCount, total: 5}``
    * ``node_error`` — ``{failedNode, message}``
    * ``done``       — terminal; sent once, after which the stream closes.

    An initial ``progress`` frame is emitted on connect from the session's
    current progress so a late subscriber gets state immediately; because the
    queue retains every event (including any already-enqueued ``done``), a
    subscriber that connects after the pipeline finished still receives the full
    sequence and a terminating ``done``.

    Args:
        session_id: The session UUID from the path.

    Raises:
        HTTPException: ``404`` when the session id is unknown.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"No session found for id {session_id!r}.",
        )
    return StreamingResponse(
        _session_event_stream(session),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


class ConfirmLetterRequest(BaseModel):
    """Body for ``POST /sessions/{id}/confirm`` — the current edited letter.

    The single ``letter`` field carries the text exactly as the user edited it
    on the Results screen. The key name matches the frontend contract
    (``frontend/src/lib/api.ts`` posts ``{ letter: <text> }``). ``extra`` is left
    at pydantic's default-ignore so an incidental extra key never 500s the
    confirm gate. The field itself is unconstrained here; the length bound is
    enforced in the handler so we can return a clear ``400`` message (Req 8.3)
    rather than a 422 validation blob.
    """

    letter: str = Field(..., description="The current (edited) appeal-letter text.")


@app.post("/sessions/{session_id}/confirm")
async def confirm_letter_endpoint(session_id: str, body: ConfirmLetterRequest):
    """Record the user's confirmed appeal letter — the explicit confirm gate.

    This endpoint is the ONLY way to confirm/transmit a letter: there is no
    implicit or automatic send anywhere in the system, so confirming is always
    an explicit, user-initiated action (Req 8.5 — "require an explicit confirm
    control before any send/export"). The client calls it only after the user
    activates the confirm control on the Results screen.

    What "transmit" means here (Req 8.6, 8.7): this MVP has no real external
    "send" integration (no email/fax/portal). "Transmit" therefore means the
    backend ACCEPTS and RECORDS the confirmed letter text as the final,
    user-approved version and returns success. The recorded text is EXACTLY the
    current edited text received in the request body — the backend never
    substitutes the Writer's original draft or any other version (Req 8.6/8.7).

    Validation:

    * Unknown ``session_id`` -> ``404`` (no letter is recorded).
    * ``letter`` longer than :data:`MAX_LETTER_CHARS` (20,000) -> ``400`` with a
      clear message, mirroring the editable field's bound (Req 8.3); nothing is
      recorded. A length at or below the bound (including empty) is accepted.

    On success the confirmed text is stored on the session
    (``session.confirmed_letter``) and the handler returns
    ``{"status": "confirmed", "length": <char count>}`` so the client can
    confirm the backend holds the exact text it sent.

    Args:
        session_id: The session UUID from the path.
        body: The :class:`ConfirmLetterRequest` carrying the edited ``letter``.

    Raises:
        HTTPException: ``404`` for an unknown session; ``400`` when the letter
            exceeds the 20,000-character bound.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"No session found for id {session_id!r}.",
        )

    letter = body.letter
    if len(letter) > MAX_LETTER_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Appeal letter is too long ({len(letter)} characters). "
                f"The maximum is {MAX_LETTER_CHARS} characters."
            ),
        )

    # Record the confirmed text verbatim (Req 8.6/8.7): this is exactly the
    # edited text received, not the original draft.
    session.confirmed_letter = letter
    return {"status": "confirmed", "length": len(letter)}


class AskRequest(BaseModel):
    """Body for ``POST /sessions/{id}/ask`` — a single question about the bill.

    The ``question`` key matches the frontend contract
    (``frontend/src/lib/api.ts`` posts ``{ question: <text> }`` and expects
    ``{ answer }`` back). The field is unconstrained at the pydantic layer so the
    length/emptiness rules (Req 10.3) are enforced in the handler, which lets us
    return a clear ``400`` message rather than a 422 validation blob.
    """

    question: str = Field(..., description="The user's question about the bill.")


def _build_ask_context(session) -> str:
    """Build the system/context prompt grounding an answer in THIS bill.

    The context is assembled from the session's analysis so the LLM answers
    about the specific uploaded bill rather than in the abstract:

    * the bill's provider and its line items (id, description, amount) — the
      concrete charges the user is asking about;
    * the retained findings (type + description) — the issues the pipeline
      surfaced, so answers can reference detected errors/rights concerns;
    * the Summary entries and the appeal-letter draft, when present, so the
      model can speak to what was already written.

    Only analysis-derived, non-sensitive fields are included. Nothing here
    touches the LLM API key or any credential — the provider holds the key and
    this function never sees it. The answer language is pinned to the session's
    selected language (Req 1.4) so Q&A matches the rest of the output.
    """
    state = session.state
    lines: list[str] = [
        "You are a patient-advocate assistant answering questions about ONE "
        "specific, already-analyzed synthetic medical bill. Answer ONLY from "
        "the bill and analysis provided below; if the answer is not supported "
        "by this context, say you do not have that information. Keep answers "
        "concise and in plain language.",
        f"Answer in this language code: {session.language.value}.",
    ]

    bill = state.bill
    if bill is not None:
        if bill.provider_name:
            lines.append(f"Provider: {bill.provider_name}")
        if bill.stated_total is not None:
            lines.append(f"Stated total: {bill.stated_total}")
        if bill.line_items:
            lines.append("Line items:")
            for item in bill.line_items:
                amount = item.amount if item.amount is not None else "n/a"
                lines.append(f"- [{item.id}] {item.description} (amount: {amount})")
        else:
            lines.append("Line items: none were extracted.")
    else:
        lines.append("No bill has been extracted yet.")

    findings = state.retained_findings or state.findings
    if findings:
        lines.append("Findings (issues detected in this bill):")
        for finding in findings:
            lines.append(f"- {finding.type.value}: {finding.description}")
    else:
        lines.append("Findings: none were retained for this bill.")

    result = state.result
    if result is not None:
        if result.summary_entries:
            lines.append("Summary:")
            for entry in result.summary_entries:
                lines.append(f"- {entry}")
        if result.appeal_letter:
            lines.append("Draft appeal letter:")
            lines.append(result.appeal_letter)

    return "\n".join(lines)


@app.post("/sessions/{session_id}/ask")
async def ask_question_endpoint(session_id: str, body: AskRequest, request: Request):
    """Answer a question about the analyzed bill (Req 10.3, 10.6, 10.7).

    Validation and error model (chosen to fit the frontend ``ask()`` contract,
    which turns any non-2xx into an ``ApiError`` carrying ``detail`` that the UI
    shows, and retains any prior answer per Req 10.7):

    * Unknown ``session_id``                      -> ``404``.
    * Empty/whitespace-only or >500-char question -> ``400`` with a clear
      message (Req 10.3). The 1–500 bound is enforced on the trimmed text.
    * No bill extracted yet (nothing to ground an answer in) -> ``409``
      "analysis not ready"; a partially-analyzed bill (bill present, pipeline
      still running) is answered from whatever is available.
    * Provider answer exceeds 30 seconds (Req 10.7)         -> ``504`` timeout.
    * Provider call fails/raises                             -> ``502``.
    * Success                                                -> ``200``
      ``{"answer": <text>}``.

    The answer is grounded in THIS bill via :func:`_build_ask_context` (provider,
    line items, findings, summary/appeal letter), then produced by the shared
    provider. ``get_provider`` is read via the ``sessions`` module attribute so
    tests can monkeypatch a mock; the blocking provider call runs off the event
    loop (``asyncio.to_thread``) under a 30-second ``asyncio.wait_for`` bound so a
    slow or hanging provider can never block the server and always yields a
    timeout error within ~30s (Req 10.6, 10.7).

    Node routing: there is no dedicated pipeline node for Q&A, so the call is
    routed through ``NodeName.WRITER`` — Q&A is prose generation like the Writer,
    and the config's writer model override (if any) is the sensible model for
    user-facing prose.

    Args:
        session_id: The session UUID from the path.
        body: The :class:`AskRequest` carrying the user's ``question``.

    Raises:
        HTTPException: ``404`` unknown session; ``400`` invalid question length;
            ``409`` analysis not ready; ``504`` answer timeout; ``502`` provider
            failure.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"No session found for id {session_id!r}.",
        )

    # Validate the question length on the trimmed text (Req 10.3): reject
    # empty/whitespace-only and anything over 500 characters.
    question = body.question.strip()
    if len(question) < MIN_QUESTION_CHARS:
        raise HTTPException(
            status_code=400,
            detail="Question must not be empty.",
        )
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Question is too long ({len(question)} characters). "
                f"The maximum is {MAX_QUESTION_CHARS} characters."
            ),
        )

    # Need an extracted bill to ground the answer. If none exists yet, the
    # analysis is not ready to answer questions about (Req 10 context).
    if session.state.bill is None:
        raise HTTPException(
            status_code=409,
            detail="The bill analysis is not ready yet. Try again shortly.",
        )

    # Cost protection: 20 questions per IP per hour (counted only once the
    # question is valid and answerable).
    if not rate_limit.limiter.allow_question(rate_limit.client_ip(request)):
        raise rate_limit.demo_limit_error()

    system = _build_ask_context(session)
    # Read get_provider off the module (not a bound import) so tests can
    # monkeypatch sessions.get_provider to inject a mock provider.
    provider = sessions.get_provider()

    def _complete() -> str:
        # Blocking provider call; bounded by the socket timeout it honors and,
        # defensively, by the asyncio.wait_for below.
        return provider.complete(
            system=system,
            user=question,
            timeout_s=ASK_TIMEOUT_SECONDS,
            node=NodeName.WRITER,
        )

    try:
        answer = await asyncio.wait_for(
            asyncio.to_thread(_complete),
            timeout=ASK_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("Ask timed out: session=%s", session_id)
        # Exceeded the 30s bound (Req 10.7). 504 so the frontend shows a clear
        # "could not retrieve answer" and keeps any prior answer.
        raise HTTPException(
            status_code=504,
            detail=(
                "The answer could not be retrieved within 30 seconds. "
                "Please try again."
            ),
        )
    except Exception as exc:
        # Provider failure. Do not leak provider internals or the API key; the
        # message is generic and safe to surface to the client. The detail is
        # logged server-side only.
        logger.warning(
            "Ask provider call failed: session=%s %s: %s",
            session_id, type(exc).__name__, exc,
        )
        raise HTTPException(
            status_code=502,
            detail="The answer could not be retrieved due to a provider error.",
        )

    return {"answer": answer}


class TranslateLetterRequest(BaseModel):
    """Body for ``POST /sessions/{id}/translate-letter`` — the English letter.

    The frontend sends the CURRENT (possibly edited) letter text so the
    translation matches what the patient sees. Length rules are enforced in the
    handler for a clear ``400`` message.
    """

    letter: str = Field(..., description="The English appeal letter to translate.")


@app.post("/sessions/{session_id}/translate-letter")
async def translate_letter_endpoint(
    session_id: str, body: TranslateLetterRequest, request: Request
):
    """Translate the English appeal letter into the session language.

    The appeal letter is always written in English (it is what gets sent). This
    read-only translation only helps a non-English-speaking patient understand
    it; it is returned to the caller and never stored on the session.

    * Unknown ``session_id``                         -> ``404``.
    * Session language is English                    -> ``400`` (nothing to do).
    * Empty/whitespace-only or >20,000-char letter   -> ``400``.
    * Provider exceeds the 60s bound                 -> ``504``.
    * Provider failure / empty completion            -> ``502`` (generic message).
    * Success -> ``200`` ``{"translation": <text>, "language": <code>}``.

    ``get_provider`` is read via the ``sessions`` module so tests can inject a
    mock; the blocking call runs off the event loop under ``asyncio.wait_for``.
    """
    session = get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"No session found for id {session_id!r}.",
        )

    if session.language == SupportedLanguage.EN:
        raise HTTPException(
            status_code=400,
            detail="This session's language is English, so no translation is needed.",
        )

    letter = body.letter.strip()
    if not letter:
        raise HTTPException(status_code=400, detail="Letter must not be empty.")
    if len(letter) > MAX_LETTER_CHARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Letter is too long ({len(letter)} characters). "
                f"The maximum is {MAX_LETTER_CHARS} characters."
            ),
        )

    # Cost protection: 10 translations per IP per hour (valid requests only).
    if not rate_limit.limiter.allow_translation(rate_limit.client_ip(request)):
        raise rate_limit.demo_limit_error()

    provider = sessions.get_provider()
    try:
        translation = await asyncio.wait_for(
            asyncio.to_thread(translate_letter, letter, session.language, provider),
            timeout=TRANSLATE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("Translation timed out: session=%s", session_id)
        raise HTTPException(
            status_code=504,
            detail="The translation took too long. Please try again.",
        )
    except Exception as exc:
        # Do not leak provider internals or the API key; log the detail only.
        logger.warning(
            "Translation provider call failed: session=%s %s: %s",
            session_id, type(exc).__name__, exc,
        )
        raise HTTPException(
            status_code=502,
            detail="The translation could not be generated due to a provider error.",
        )

    return {"translation": translation, "language": session.language.value}


@app.post("/eval/run")
async def run_evaluation_endpoint():
    """Run the detection-quality evaluation over the Eval_Set (Req 13).

    Executes the pipeline once per synthetic bill in the ten-bill Eval_Set and
    returns the aggregate :class:`~eval.models.EvalReport` as JSON:
    ``{detected, missed, false_positives, failed_bills, per_bill}`` (Req 13.6).

    Hermetic by construction (no real LLM, no API credits)
    ------------------------------------------------------
    The harness injects its OWN deterministic :class:`~eval.harness.EvalLLMProvider`
    into every pipeline run, independent of the provider the backend is
    configured with (:func:`sessions.get_provider` is never consulted here). That
    provider parses each bill's embedded line format for the Extractor and
    returns canned prose for the Writer, so the Extractor's structuring and the
    Writer's generation make no network calls, while Sum_Check, Auditor,
    Rights_Checker (real BM25 KB), and Skeptic all run for real. The run is
    therefore fully deterministic and consumes no API credits. It executes ten
    pipeline runs, so it takes a little time, but it is bounded and repeatable.

    Status codes:

    * ``200`` — the report, as the JSON body.
    * ``400`` — the Eval_Set is empty (Req 13.2); the message states the set is
      empty. (The default ten-bill set is never empty, so this only arises if the
      underlying set is constructed empty.)

    The blocking evaluation runs off the event loop (``asyncio.to_thread``) so it
    never stalls the server while the ten pipelines execute.

    Raises:
        HTTPException: ``400`` when the Eval_Set is empty (Req 13.2).
    """
    try:
        report = await asyncio.to_thread(run_evaluation)
    except EmptyEvalSetError as exc:
        # Req 13.2: an empty Eval_Set terminates without running the pipeline.
        raise HTTPException(status_code=400, detail=str(exc))

    # Serialize via the pydantic model so the shape is the canonical EvalReport.
    return report.model_dump(mode="json")
