"""Evaluation data models for the synthetic Eval_Set (Req 12, 13).

These Pydantic v2 models describe the ten-bill synthetic evaluation set and the
shape of the detection-quality report. They follow the same conventions as
``backend/models.py``: ``extra="forbid"`` so malformed manifest entries yield
precise field errors rather than being silently accepted.

Key invariants (enforced here and in :mod:`backend.eval.eval_set.loader`):

- ``PlantedError.expected_type`` is REQUIRED (no default). A manifest entry for
  a planted error that omits the expected Finding type fails validation, and the
  loader re-raises a bill-identifying error (Req 12.4, 12.5).
- ``PlantedError.id`` uniquely links a planted error to its location within a
  bill (Req 12.4); the loader additionally enforces uniqueness of these ids.
- ``EvalBill.synthetic`` defaults to ``True`` and marks the bill as synthetic
  data (Req 12.1 / 12.2).
- ``EvalBill.planted_errors`` holds between 1 and 10 planted errors (Req 12.3).

Matching logic (Req 13.4 / 13.5) that compares a retained ``Finding``'s ``type``
and referenced ``line_item_ids`` against each planted error's ``expected_type``
and ``line_item_id`` lives in the harness (task 16.2); this module only defines
the data shapes and the ``EvalReport`` totals (Req 13.6, 13.3).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from models import FindingType


class PlantedError(BaseModel):
    """A single deliberately-planted error within a synthetic bill (Req 12.4).

    ``id`` is a unique identifier linking the planted error to its location in
    the bill. ``expected_type`` is the Finding type the pipeline is expected to
    produce for this error; it is REQUIRED so that a manifest omitting it fails
    validation and the Eval_Set is treated as invalid (Req 12.5).
    ``line_item_id`` names the specific ``LineItem`` the error sits on, when the
    error is tied to a particular line (used by the task 16.2 matcher).
    """

    model_config = ConfigDict(extra="forbid")

    id: str  # unique link to the error's location within the bill (Req 12.4)
    expected_type: FindingType  # REQUIRED expected Finding type (Req 12.4, 12.5)
    line_item_id: Optional[str] = None  # referenced line, when applicable


class EvalBill(BaseModel):
    """A single synthetic bill in the Eval_Set with its planted errors (Req 12).

    ``synthetic`` marks the bill as synthetic data and defaults to ``True``
    (Req 12.1 / 12.2). ``pdf_path`` is the path (relative to the eval_set
    directory in the manifest; absolute once resolved by the loader) to the
    text-bearing synthetic PDF. ``planted_errors`` carries 1-10 planted errors
    (Req 12.3).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    synthetic: bool = True  # Req 12.1 / 12.2 synthetic marker
    pdf_path: str
    planted_errors: list[PlantedError] = Field(min_length=1, max_length=10)  # Req 12.3


class EvalReport(BaseModel):
    """Aggregate detection-quality report over the Eval_Set (Req 13).

    ``detected`` / ``missed`` / ``false_positives`` are the aggregate counts
    across all bills (Req 13.6); ``failed_bills`` counts bills whose pipeline run
    did not complete (Req 13.3). ``per_bill`` holds one entry per bill with its
    own breakdown, letting the aggregate totals be reconciled against the
    per-bill counts (design Property 16).
    """

    model_config = ConfigDict(extra="forbid")

    detected: int = Field(ge=0)
    missed: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    failed_bills: int = Field(ge=0)  # Req 13.3
    per_bill: list[dict] = Field(default_factory=list)
