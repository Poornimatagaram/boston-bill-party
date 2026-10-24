"""Loader and validator for the synthetic Eval_Set (Req 12).

:func:`load_eval_set` reads ``manifest.json`` from this directory, validates
each entry against :class:`~eval.models.EvalBill`, and returns the list of
``EvalBill`` objects with ``pdf_path`` resolved to an absolute path. Validation
is wrapped so every failure names the offending bill:

- A planted error missing its ``expected_type`` (or any other schema violation)
  raises :class:`EvalSetError` identifying the bill (Req 12.4, 12.5).
- The set must contain EXACTLY ten bills (Req 12.3); otherwise :class:`EvalSetError`.
- Each bill carries 1-10 planted errors (enforced by the model, Req 12.3) and
  every planted-error ``id`` must be unique within its bill (the ids link a
  planted error to its location, Req 12.4).

The loader never processes real data: it only reads the committed synthetic
manifest and confirms each bill is marked ``synthetic`` (Req 12.1 / 12.2).
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import ValidationError

from eval.models import EvalBill

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MANIFEST_PATH = os.path.join(HERE, "manifest.json")

# The Eval_Set contains exactly this many synthetic bills (Req 12.3).
EXPECTED_BILL_COUNT = 10


class EvalSetError(Exception):
    """Raised when the Eval_Set is invalid (Req 12.3, 12.4, 12.5)."""


def _bill_identifier(entry: Any, index: int) -> str:
    """Best-effort human-readable identifier for an offending manifest entry."""
    if isinstance(entry, dict):
        bill_id = entry.get("id")
        if isinstance(bill_id, str) and bill_id:
            return bill_id
    return f"<bill at index {index}>"


def _validate_entry(entry: Any, index: int) -> EvalBill:
    """Validate a single manifest entry into an ``EvalBill`` (naming the bill).

    Wraps pydantic ``ValidationError`` into an :class:`EvalSetError` that names
    the bill and, when the failure is a missing ``expected_type`` on a planted
    error, states that explicitly (Req 12.5).
    """
    identifier = _bill_identifier(entry, index)
    try:
        return EvalBill.model_validate(entry)
    except ValidationError as exc:
        missing_expected_type = any(
            err.get("type") == "missing"
            and "expected_type" in tuple(err.get("loc", ()))
            for err in exc.errors()
        )
        if missing_expected_type:
            raise EvalSetError(
                f"Eval_Set is invalid: bill '{identifier}' has a planted error "
                "with no recorded expected Finding type."
            ) from exc
        details = "; ".join(
            f"{'.'.join(str(p) for p in err.get('loc', ())) or '<root>'}: "
            f"{err.get('msg', 'invalid')}"
            for err in exc.errors()
        )
        raise EvalSetError(
            f"Eval_Set is invalid: bill '{identifier}' failed validation: {details}"
        ) from exc


def _check_planted_error_ids(bill: EvalBill) -> None:
    """Ensure planted-error ids are unique within the bill (Req 12.4)."""
    seen: set[str] = set()
    for err in bill.planted_errors:
        if err.id in seen:
            raise EvalSetError(
                f"Eval_Set is invalid: bill '{bill.id}' has a duplicate planted "
                f"error id '{err.id}'; planted-error ids must be unique."
            )
        seen.add(err.id)


def load_eval_set(manifest_path: str | None = None) -> list[EvalBill]:
    """Load, validate, and return the ten synthetic ``EvalBill``s (Req 12).

    Reads the manifest (defaulting to ``manifest.json`` beside this module),
    validates every entry naming any offending bill, enforces exactly ten bills
    (Req 12.3) and unique planted-error ids per bill (Req 12.4), confirms each
    bill is marked synthetic (Req 12.1 / 12.2), and resolves ``pdf_path`` to an
    absolute path.

    Raises:
        EvalSetError: if the manifest is unreadable/malformed, a bill fails
            validation (including a planted error lacking an expected Finding
            type, Req 12.5), the bill count is not ten, a planted-error id is
            duplicated, or a bill is not marked synthetic.
    """
    path = manifest_path or DEFAULT_MANIFEST_PATH
    base_dir = os.path.dirname(os.path.abspath(path))

    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError as exc:
        raise EvalSetError(f"Eval_Set manifest not found at {path}.") from exc
    except json.JSONDecodeError as exc:
        raise EvalSetError(
            f"Eval_Set manifest at {path} is not valid JSON: {exc}"
        ) from exc

    if not isinstance(raw, dict) or not isinstance(raw.get("bills"), list):
        raise EvalSetError(
            "Eval_Set manifest must be an object with a 'bills' list."
        )

    entries = raw["bills"]
    bills: list[EvalBill] = []
    seen_bill_ids: set[str] = set()
    for index, entry in enumerate(entries):
        bill = _validate_entry(entry, index)

        if not bill.synthetic:
            raise EvalSetError(
                f"Eval_Set is invalid: bill '{bill.id}' is not marked synthetic "
                "(Req 12.1/12.2)."
            )
        if bill.id in seen_bill_ids:
            raise EvalSetError(
                f"Eval_Set is invalid: duplicate bill id '{bill.id}'."
            )
        seen_bill_ids.add(bill.id)

        _check_planted_error_ids(bill)

        # Resolve the PDF path relative to the manifest directory.
        resolved = bill.pdf_path
        if not os.path.isabs(resolved):
            resolved = os.path.normpath(os.path.join(base_dir, resolved))
        bills.append(bill.model_copy(update={"pdf_path": resolved}))

    if len(bills) != EXPECTED_BILL_COUNT:
        raise EvalSetError(
            f"Eval_Set must contain exactly {EXPECTED_BILL_COUNT} bills "
            f"(Req 12.3); found {len(bills)}."
        )

    return bills
