"""In-memory session registry and background pipeline task (Req 1.1, 1.7).

Design ("Session State and Concurrency"): each upload creates an in-memory
:class:`Session` keyed by a UUID, holding the selected language, pipeline
status, per-node progress, the evolving :class:`~pipeline.state.PipelineState`,
and an ``asyncio.Queue`` of SSE events that the ``GET /sessions/{id}/events``
endpoint (task 14.1) drains. The pipeline runs as a BACKGROUND async task
started when the session is created, so ``POST /sessions`` (task 8.1) returns
immediately with the session id (Req 1.7) while progress streams independently.

Sessions are process-local and ephemeral: no persistence, synthetic data only
(Req 12.1). The registry is a plain module-level dict — if the process restarts,
sessions are gone, which is the intended behavior.

Concurrency model
-----------------
``run_pipeline`` (pipeline/graph.py) is a SYNCHRONOUS, blocking runner
(pdfplumber reads the PDF; the provider makes blocking HTTP calls). Running it
directly on the event loop would stall every other request, so the background
task offloads it to a worker thread via :func:`asyncio.to_thread`. The runner's
``progress`` callback therefore fires *from that worker thread*; it must hand
events back to the loop thread safely. We capture the running loop at session
creation and use ``loop.call_soon_threadsafe(queue.put_nowait, event)`` to
enqueue each event. The queue is unbounded, so ``put_nowait`` never raises.

SSE event shapes (design "SSE Event Shapes"; carried on the queue so task 14.1
only has to serialize and stream them):

* ``progress``   — ``{activeNode, completedNodes: [str], completedCount, total: 5}``
* ``node_error`` — ``{failedNode, message}``
* ``done``       — terminal sentinel

Each queue item is a ``(event_type, data)`` tuple. A terminal ``("done", None)``
is always the last item, so the SSE consumer can stop draining.

Provider / config
-----------------
The LLM provider is built once (``build_provider(load_config())``) and cached at
module level, then injected into every ``run_pipeline`` call. Config load
failure is a startup concern (task 2.1): it surfaces as a clear error from
:func:`get_provider`, but the message never contains the API key (``ConfigError``
and ``LLMConfig`` are both built to keep the secret out of their text). This
module never reads, logs, or prints the key — it only passes the provider
through.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from config import load_config
from llm.provider import LLMProvider, build_provider
from pipeline.graph import NODE_ORDER, TOTAL_NODES, run_pipeline
from pipeline.state import NodeStatus, PipelineState
from models import SupportedLanguage

# A queue item: (event_type, data). ``data`` is a JSON-serializable dict for
# "progress"/"node_error", and ``None`` for the terminal "done" sentinel.
QueueItem = tuple[str, Optional[dict]]


class SessionStatus(str, Enum):
    """Lifecycle of a session's background pipeline (Req 1.1).

    ``RUNNING`` from creation until the pipeline finishes; ``COMPLETED`` when the
    Writer produced a result; ``ERROR`` when a node raised a node-fatal error
    (``PipelineState.error`` is set). A str-backed Enum so values serialize
    directly into JSON responses.
    """

    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


def _progress_event(state: PipelineState) -> dict:
    """Build a ``progress`` event payload from the current node statuses.

    ``activeNode`` is the single node currently RUNNING (``None`` if none is, e.g.
    between two transitions); ``completedNodes`` is every node marked COMPLETED in
    canonical :data:`~pipeline.graph.NODE_ORDER`; ``completedCount`` is its length;
    ``total`` is the fixed five-node denominator (design "SSE Event Shapes").
    """
    active: Optional[str] = None
    completed: list[str] = []
    for node in NODE_ORDER:
        status = state.node_status.get(node.value, NodeStatus.PENDING)
        if status == NodeStatus.RUNNING:
            active = node.value
        elif status == NodeStatus.COMPLETED:
            completed.append(node.value)
    return {
        "activeNode": active,
        "completedNodes": completed,
        "completedCount": len(completed),
        "total": TOTAL_NODES,
    }


@dataclass
class Session:
    """In-memory state for one upload's pipeline run (Req 1.1).

    Attributes:
        id: The session UUID (string), the registry key.
        language: The selected output language for the pipeline.
        status: :class:`SessionStatus`, updated by the background task.
        state: The evolving :class:`PipelineState` (per-node progress lives in
            ``state.node_status``; the result/error live on it too).
        queue: Async queue of SSE event tuples the events endpoint drains.
        task: Handle to the background pipeline task (set on launch).
        confirmed_letter: The exact edited letter text the user confirmed via
            ``POST /sessions/{id}/confirm`` (task 10.3), or ``None`` until the
            explicit confirm gate fires (Req 8.5–8.7). Defaults to ``None`` so
            existing ``Session(...)`` construction (and tests) stay unchanged.
    """

    id: str
    language: SupportedLanguage
    state: PipelineState
    queue: "asyncio.Queue[QueueItem]" = field(default_factory=asyncio.Queue)
    status: SessionStatus = SessionStatus.RUNNING
    task: Optional["asyncio.Task[None]"] = None
    confirmed_letter: Optional[str] = None

    @property
    def progress(self) -> dict:
        """Current per-node progress, derived from ``state.node_status``.

        Mirrors the ``progress`` SSE payload so a late subscriber (or the result
        endpoint) can read the live picture without draining the queue.
        """
        return _progress_event(self.state)

    @property
    def result(self):
        """The Writer's ``AnalysisResult`` once produced, else ``None``."""
        return self.state.result

    @property
    def error(self) -> Optional[dict]:
        """``{failedNode, message}`` on a node-fatal error, else ``None`` (Req 9.4)."""
        return self.state.error


# --- Module-level registry (process-local, ephemeral; Req 12.1) -------------
_SESSIONS: dict[str, Session] = {}

# Lazily-built, cached provider. Built once from the validated config and
# injected into every pipeline run (Req 11.4). ``None`` until first requested.
_PROVIDER: Optional[LLMProvider] = None


def get_provider() -> LLMProvider:
    """Return the shared LLM provider, building it once on first use (Req 11.4).

    Builds from ``build_provider(load_config())`` and caches the result so every
    session reuses one provider. A configuration error (task 2.1) propagates
    unchanged; its message names the offending variable(s) but never the API key
    value. This is intentionally lazy so importing this module does not require a
    valid ``.env`` (keeping unit-import checks cheap).
    """
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = build_provider(load_config())
    return _PROVIDER


def get_session(session_id: str) -> Optional[Session]:
    """Return the registered :class:`Session`, or ``None`` if unknown."""
    return _SESSIONS.get(session_id)


def _make_progress_bridge(
    loop: asyncio.AbstractEventLoop, queue: "asyncio.Queue[QueueItem]"
):
    """Build the ``run_pipeline`` progress callback that feeds the async queue.

    The returned callback fires from the worker thread (``run_pipeline`` runs
    under :func:`asyncio.to_thread`), so it must not touch the queue directly.
    Instead it schedules ``queue.put_nowait`` on the captured loop via
    ``loop.call_soon_threadsafe`` — the thread-safe bridge from worker thread to
    event loop. Only RUNNING/COMPLETED/ERROR transitions emit an event:

    * any RUNNING/COMPLETED transition -> a ``progress`` event reflecting the new
      aggregate picture (active node + completed set);
    * an ERROR transition -> a ``node_error`` event carrying ``state.error``.

    The terminal ``done`` event is emitted by the runner coroutine after the
    pipeline returns, not here, so it always lands last.
    """

    def _put(item: QueueItem) -> None:
        # Unbounded queue -> put_nowait cannot raise QueueFull. Scheduling on the
        # loop keeps all queue mutation on the loop thread.
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def _on_progress(node_name: str, status: NodeStatus, state: PipelineState) -> None:
        if status == NodeStatus.ERROR:
            err = state.error or {"failedNode": node_name, "message": "node failed"}
            _put(("node_error", dict(err)))
            return
        if status in (NodeStatus.RUNNING, NodeStatus.COMPLETED):
            _put(("progress", _progress_event(state)))

    return _on_progress


async def _run_session_pipeline(session: Session) -> None:
    """Background task body: run the pipeline off-loop and close the queue.

    Offloads the blocking :func:`run_pipeline` to a worker thread so the event
    loop stays responsive while the PDF is parsed and the provider is called.
    The progress bridge streams ``progress``/``node_error`` events as nodes
    transition; once the runner returns we set the terminal session status from
    ``state.error`` and always enqueue a final ``done`` sentinel so SSE consumers
    stop. A defensive catch converts an unexpected background failure into a
    ``node_error`` + ``done`` pair instead of a silently-dropped task exception.
    """
    loop = asyncio.get_running_loop()
    progress = _make_progress_bridge(loop, session.queue)
    state = session.state
    try:
        await asyncio.to_thread(
            run_pipeline,
            state,
            session.provider,  # injected provider (holds credentials)
            session.pdf_source,
            progress,
        )
        if state.error is not None:
            session.status = SessionStatus.ERROR
        else:
            session.status = SessionStatus.COMPLETED
    except Exception as exc:  # defensive: never drop a background exception
        # Record a node-fatal-shaped error if the runner itself blew up before
        # setting one. The API key is never part of these messages.
        if state.error is None:
            state.error = {"failedNode": "pipeline", "message": str(exc)}
            session.queue.put_nowait(("node_error", dict(state.error)))
        session.status = SessionStatus.ERROR
    finally:
        # Terminal sentinel always lands last so consumers can stop draining.
        session.queue.put_nowait(("done", None))


def create_session(
    pdf_source: Any,
    language: SupportedLanguage,
    *,
    provider: Optional[LLMProvider] = None,
) -> str:
    """Create a session, launch its background pipeline, and return the id.

    Constructs the :class:`PipelineState`, registers the :class:`Session`, and
    starts the pipeline as a background :class:`asyncio.Task`. It returns
    immediately — the (blocking) pipeline runs on a worker thread — so the caller
    (``POST /sessions``, task 8.1) can respond fast with the session id (Req 1.7).

    Args:
        pdf_source: Anything ``pdfplumber.open`` accepts — a filesystem path or a
            binary stream (e.g. ``io.BytesIO`` of the uploaded bytes).
        language: The selected output language (Req 1.4).
        provider: Optional explicit provider (used by tests to inject a mock).
            When ``None``, the shared cached provider from :func:`get_provider`
            is built/reused.

    Returns:
        The new session's UUID string (Req 1.1).

    Raises:
        RuntimeError: if called without a running event loop (the background
            launch requires one). Config errors from the default provider build
            propagate from :func:`get_provider` (never leaking the API key).
    """
    session_id = str(uuid.uuid4())
    state = PipelineState(session_id=session_id, language=language)
    session = Session(id=session_id, language=language, state=state)

    # Stash the run inputs on the session for the background task. Kept as plain
    # attributes (not dataclass fields) so the Session's public shape stays the
    # documented state/queue/status surface.
    session.provider = provider if provider is not None else get_provider()
    session.pdf_source = pdf_source

    _SESSIONS[session_id] = session
    # Launch the background task. create_task requires a running loop; the error
    # it raises otherwise is clear enough for the API layer to surface.
    session.task = asyncio.create_task(_run_session_pipeline(session))
    return session_id


def clear_sessions() -> None:
    """Remove all registered sessions (test/teardown helper).

    Ephemeral by design (Req 12.1); exposed so tests can reset module state
    between cases without reaching into the private registry.
    """
    _SESSIONS.clear()
