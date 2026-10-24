"""Extractor node: PDF read + LLM structuring into an ``ExtractedBill`` (Req 2).

Flow (design "Node Responsibilities / Extractor"):

1. Read all PDF pages in page order with pdfplumber and concatenate their
   text (Req 2.1).
2. If no extractable text remains after reading every page, raise a
   node-fatal "no readable text" error and do NOT invoke the LLM (Req 2.7).
3. Otherwise call the configured ``LLMProvider`` with a 60s timeout (Req 2.8)
   to turn the raw text into JSON, parse it, and validate it against the
   Pydantic ``ExtractedBill`` schema (Req 2.2).
4. On validation failure raise a node-fatal error that names each
   nonconforming field and the reason it failed, with no partial object
   produced (Req 2.6).
5. Missing optional line-item fields become ``null`` (Req 2.4); monetary
   fields (amount, unit_price) are normalized to non-negative two-decimal
   ``Decimal`` and quantity to a non-negative numeric value (Req 2.5).

Error handling is node-fatal: failures raise :class:`ExtractorError`, which the
pipeline turns into ``PipelineState.error = {failedNode, message}`` and an SSE
``node_error`` event (see design "Node-fatal propagation"). The successful path
returns a fully-validated :class:`ExtractedBill`.

Security note: this module never reads or references the LLM API key. The
injected provider holds the key; nothing here logs request/response bodies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Protocol

import pdfplumber
from pydantic import ValidationError

from models import ExtractedBill

# Node identifier for this stage. Task 2.1 will introduce a shared ``NodeName``
# literal/enum in ``config.py``; until then this string is the stable node id
# used for the provider call and for node-fatal error reporting. It matches the
# value a future ``NodeName.EXTRACTOR`` is expected to carry.
NODE_NAME = "extractor"

# Req 2.8: the Extractor calls the LLM with a hard 60-second timeout.
EXTRACTOR_TIMEOUT_S: float = 60.0

# Money is normalized to exactly two decimal places (Req 2.5).
_TWO_PLACES = Decimal("0.01")

# Optional line-item fields that must become ``null`` when absent (Req 2.4).
_OPTIONAL_LINE_ITEM_FIELDS = ("billing_code", "quantity", "unit_price", "amount")

# Fields normalized as non-negative two-decimal money (Req 2.5).
_MONEY_FIELDS = ("unit_price", "amount")
# Fields normalized as a non-negative numeric value (Req 2.5).
_QUANTITY_FIELDS = ("quantity",)


class LLMProvider(Protocol):
    """Minimal protocol for the injected LLM provider (see design Req 11).

    Mirrors ``llm/provider.py`` (task 2.2). Accepting the provider as an
    argument keeps the node testable with a mock and means the extractor never
    touches credentials.
    """

    def complete(
        self, *, system: str, user: str, timeout_s: float, node: Any
    ) -> str: ...


@dataclass(frozen=True)
class FieldError:
    """A single field that failed ``ExtractedBill`` validation (Req 2.6)."""

    field: str  # dotted path, e.g. "line_items.0.amount"
    reason: str  # human-readable reason the field failed


@dataclass
class ExtractorError(Exception):
    """Node-fatal error raised by the Extractor.

    ``field_errors`` is populated only for schema-validation failures (Req 2.6);
    it is empty for the no-readable-text (Req 2.7) and timeout (Req 2.8) cases.
    No partial ``ExtractedBill`` is ever attached.
    """

    message: str
    field_errors: list[FieldError] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message

    def to_state_error(self) -> dict:
        """Shape used for ``PipelineState.error`` ({failedNode, message})."""
        return {"failedNode": NODE_NAME, "message": self.message}


# System prompt instructing the provider to emit ExtractedBill-shaped JSON.
_SYSTEM_PROMPT = (
    "You convert raw medical-bill text into a single JSON object matching this "
    "schema exactly, with no extra keys: {provider_name, provider_address, "
    "account_number, line_items: [{id, description, billing_code, quantity, "
    "unit_price, amount}], stated_total}. Use null for any value not present in "
    "the text. Monetary values must be non-negative numbers. Return only the "
    "JSON object, no prose and no markdown fences."
)


def read_pdf_text(pdf_source: Any) -> str:
    """Read every page of a PDF in page order and concatenate its text (Req 2.1).

    ``pdf_source`` is anything ``pdfplumber.open`` accepts (a path or a binary
    file-like object). Pages are joined with newlines in page order. Returns the
    concatenated text (which may be empty / whitespace-only when the PDF has no
    extractable text).
    """
    parts: list[str] = []
    with pdfplumber.open(pdf_source) as pdf:
        for page in pdf.pages:  # pdf.pages is already in page order
            text = page.extract_text()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _strip_code_fences(raw: str) -> str:
    """Tolerate a provider wrapping JSON in ``` fences despite instructions."""
    text = raw.strip()
    if text.startswith("```"):
        text = text[len("```") :].lstrip()
        # Drop an optional language tag on the first line (e.g. ```json).
        if "\n" in text:
            first_line, rest = text.split("\n", 1)
            if first_line.strip().lower() in {"json", ""}:
                text = rest
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -len("```")]
    return text.strip()


def _normalize_money(value: Any) -> Any:
    """Coerce a value to a non-negative two-decimal ``Decimal`` (Req 2.5).

    ``None`` passes through (optional field stays null, Req 2.4). Negative
    magnitudes are made non-negative via absolute value so downstream
    ``ge=0`` validation holds. Unparseable values are returned unchanged so the
    schema validator reports a precise field error (Req 2.6).
    """
    if value is None:
        return None
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value  # let ExtractedBill validation name the bad field
    if not dec.is_finite():
        return value
    return abs(dec).quantize(_TWO_PLACES)


def _normalize_quantity(value: Any) -> Any:
    """Coerce quantity to a non-negative numeric ``Decimal`` (Req 2.5).

    Unlike money, quantity is not forced to two decimals. ``None`` passes
    through; unparseable values are returned unchanged for schema reporting.
    """
    if value is None:
        return None
    try:
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return value
    if not dec.is_finite():
        return value
    return abs(dec)


def _normalize_payload(payload: Any) -> Any:
    """Apply Req 2.4 / 2.5 normalization to a parsed JSON payload in place.

    Only touches a dict with a list of line-item dicts; anything else is left
    untouched so the schema validator produces the right field errors.
    """
    if not isinstance(payload, dict):
        return payload
    line_items = payload.get("line_items")
    if not isinstance(line_items, list):
        return payload

    for item in line_items:
        if not isinstance(item, dict):
            continue
        # Req 2.4: missing optional fields become explicit null.
        for key in _OPTIONAL_LINE_ITEM_FIELDS:
            item.setdefault(key, None)
        # Req 2.5: non-negative money (2dp) and non-negative quantity.
        for key in _MONEY_FIELDS:
            item[key] = _normalize_money(item.get(key))
        for key in _QUANTITY_FIELDS:
            item[key] = _normalize_quantity(item.get(key))

    if "stated_total" in payload:
        payload["stated_total"] = _normalize_money(payload.get("stated_total"))
    return payload


def _collect_field_errors(exc: ValidationError) -> list[FieldError]:
    """Turn a pydantic ``ValidationError`` into named per-field errors (Req 2.6)."""
    errors: list[FieldError] = []
    for err in exc.errors():
        location = ".".join(str(part) for part in err.get("loc", ())) or "<root>"
        errors.append(FieldError(field=location, reason=err.get("msg", "invalid")))
    return errors


def structure_bill(raw_text: str, provider: LLMProvider) -> ExtractedBill:
    """Call the LLM and validate its output into an ``ExtractedBill``.

    Assumes ``raw_text`` is non-empty (the empty-text short-circuit lives in
    :func:`extract_bill`). Calls the provider with a 60s timeout (Req 2.8),
    parses JSON, normalizes optional/money/quantity fields (Req 2.4, 2.5), and
    validates against ``ExtractedBill`` (Req 2.2). Raises :class:`ExtractorError`
    on timeout or nonconforming output with no partial object (Req 2.6).
    """
    try:
        completion = provider.complete(
            system=_SYSTEM_PROMPT,
            user=raw_text,
            timeout_s=EXTRACTOR_TIMEOUT_S,
            node=NODE_NAME,
        )
    except TimeoutError as exc:
        # Req 2.8: provider did not respond within the timeout window.
        raise ExtractorError(
            message="Bill extraction timed out after 60 seconds."
        ) from exc

    try:
        payload = json.loads(_strip_code_fences(completion))
    except json.JSONDecodeError as exc:
        # Non-JSON output is a schema nonconformance (Req 2.6); no partial object.
        raise ExtractorError(
            message="LLM output was not valid JSON and does not conform to the "
            "ExtractedBill schema.",
            field_errors=[FieldError(field="<root>", reason=f"invalid JSON: {exc.msg}")],
        ) from exc

    payload = _normalize_payload(payload)

    try:
        return ExtractedBill.model_validate(payload)
    except ValidationError as exc:
        field_errors = _collect_field_errors(exc)
        named = "; ".join(f"{fe.field}: {fe.reason}" for fe in field_errors)
        raise ExtractorError(
            message="LLM output does not conform to the ExtractedBill schema: "
            + named,
            field_errors=field_errors,
        ) from exc


def extract_bill(pdf_source: Any, provider: LLMProvider) -> ExtractedBill:
    """Run the full Extractor node (Req 2).

    Reads the PDF (Req 2.1); if it yields no extractable text, raises the
    node-fatal "no readable text" error WITHOUT calling the provider (Req 2.7).
    Otherwise structures and validates the bill via :func:`structure_bill`.
    """
    raw_text = read_pdf_text(pdf_source)
    if not raw_text.strip():
        # Req 2.7: short-circuit before any provider invocation.
        raise ExtractorError(
            message="No readable text was found in the uploaded PDF."
        )
    return structure_bill(raw_text, provider)
