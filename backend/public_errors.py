"""User-facing error messages for node-fatal pipeline failures.

Internal errors (``PipelineState.error``, exceptions) can carry technical
detail such as an upstream HTTP status or a schema field name. That detail is
logged server-side only. Clients always receive ``{failedNode, message}`` with
a fixed, friendly message chosen by the failed node, so provider text never
reaches a user. The frontend shows its own translated text for each node; these
English strings are the API-level fallback.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

logger = logging.getLogger("boston_bill_party")

EXTRACTOR_MESSAGE = (
    "We couldn't read this bill. Please try again in a moment, or try a different PDF."
)
CHECK_MESSAGE = "Something went wrong while checking this bill. Please try again."
WRITER_MESSAGE = (
    "We couldn't write your summary and appeal letter. Please try again in a moment."
)
GENERIC_MESSAGE = "Something went wrong on our side. Please try again in a moment."

_BY_NODE = {
    "extractor": EXTRACTOR_MESSAGE,
    "auditor": CHECK_MESSAGE,
    "rights_checker": CHECK_MESSAGE,
    "skeptic": CHECK_MESSAGE,
    "writer": WRITER_MESSAGE,
}

#: Every message a client can receive for a node-fatal error.
PUBLIC_MESSAGES = frozenset({*_BY_NODE.values(), GENERIC_MESSAGE})


def public_message(failed_node: Optional[str]) -> str:
    return _BY_NODE.get(failed_node or "", GENERIC_MESSAGE)


def public_error(error: Optional[Mapping[str, Any]]) -> dict:
    """Return the client-safe ``{failedNode, message}`` for an internal error."""
    node = str((error or {}).get("failedNode") or "pipeline")
    return {"failedNode": node, "message": public_message(node)}


def log_node_error(session_id: str, error: Mapping[str, Any]) -> None:
    """Log the internal error detail. Messages never contain the API key."""
    logger.warning(
        "Pipeline failed: session=%s node=%s detail=%s",
        session_id,
        error.get("failedNode"),
        error.get("message"),
    )
