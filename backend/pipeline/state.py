"""Shared pipeline state for the Boston Bill Party pipeline (Req 9).

This module defines the single typed state object threaded through every node
of the pipeline, plus the per-node status enum used to drive live progress.

Design ("LangGraph Pipeline State"): the pipeline is a five-node concept
(Extractor -> Auditor -> Rights_Checker -> Skeptic -> Writer). Even in the MVP,
where only Extractor, Auditor and Writer are wired, ``node_status`` carries an
entry for all five conceptual nodes so the SSE progress semantics
(``completed/5``) stay consistent as the remaining nodes are inserted later
(tasks 12.3, 13.2). The two not-yet-present nodes (Rights_Checker, Skeptic) are
represented explicitly rather than omitted; see :mod:`pipeline.graph` for how
the MVP marks them.

The state shape matches the design verbatim: ``session_id``, ``language``,
``raw_text``, ``bill``, ``sum_check``, ``findings`` (auditor + rights),
``retained_findings`` (post-Skeptic), ``rights_status``, ``result``,
``node_status`` and ``error`` (``{failedNode, message}``, Req 9.4).

``NodeStatus`` is defined here (not in ``models.py``) per the task: it is a
pipeline-orchestration concern, not a wire data model. The node-name keys used
in ``node_status`` are the lowercase string values of ``config.NodeName`` so the
SSE layer and the frontend share one canonical vocabulary.

Security note: nothing in this module touches the LLM API key. The provider is
injected into the graph, not stored on the state.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from models import (
    AnalysisResult,
    ExtractedBill,
    Finding,
    RightsStatus,
    SumCheckResult,
    SupportedLanguage,
)


class NodeStatus(str, Enum):
    """Lifecycle status of a single pipeline node (drives SSE progress, Req 9).

    A node starts :data:`PENDING`, becomes :data:`RUNNING` when the graph enters
    it, and ends :data:`COMPLETED` on success or :data:`ERROR` when the node
    raised a node-fatal error (Req 9.4). A str-backed Enum so values serialize
    directly into SSE payloads and compare equal to plain strings.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"


class PipelineState(BaseModel):
    """Typed state threaded through every pipeline node (Req 9).

    One instance is created per session and mutated in place as each node runs.
    Fields map one-to-one to the design's "LangGraph Pipeline State":

    * ``session_id`` / ``language`` — set at construction from the session.
    * ``raw_text`` / ``bill`` — populated by the Extractor.
    * ``sum_check`` / ``findings`` — populated by the Auditor (the Sum_Check is
      computed at the start of the Auditor node). ``findings`` accumulates
      auditor and, later, rights Findings.
    * ``retained_findings`` — the post-Skeptic set the Writer consumes. In the
      MVP (no Skeptic) the Auditor step mirrors ``findings`` here so the Writer
      has a stable input and the Skeptic seam exists (task 13.2).
    * ``rights_status`` — defaults to COMPLETED; the Rights_Checker will set it
      (Req 5.5). Harmless in the MVP where Rights_Checker is absent.
    * ``result`` — the Writer's ``AnalysisResult``.
    * ``node_status`` — per-node :class:`NodeStatus`, keyed by node name, for all
      five conceptual nodes (see module docstring).
    * ``error`` — ``{failedNode, message}`` set on a node-fatal failure (Req 9.4),
      otherwise ``None``.
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    language: SupportedLanguage
    raw_text: Optional[str] = None
    bill: Optional[ExtractedBill] = None
    sum_check: Optional[SumCheckResult] = None
    findings: list[Finding] = Field(default_factory=list)  # auditor + rights
    retained_findings: list[Finding] = Field(default_factory=list)  # post-skeptic
    rights_status: RightsStatus = RightsStatus.COMPLETED
    result: Optional[AnalysisResult] = None
    node_status: dict[str, NodeStatus] = Field(default_factory=dict)
    error: Optional[dict] = None  # {failedNode, message} (Req 9.4)
