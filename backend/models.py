"""Pydantic v2 data models for the Boston Bill Party pipeline.

All monetary fields use ``decimal.Decimal`` and serialize as strings with
exactly two decimal places. Models are configured with ``extra="forbid"`` so
that nonconforming LLM output yields precise field errors (Req 2.6).

References:
- Req 2.3, 2.4, 2.5 (line-item structure, null optional fields, non-negative 2dp money)
- Req 3.1, 3.2, 3.5, 3.6 (sum-check result, math-error findings)
- Req 4.6, 5.3 (confidence in [0, 1])
- Req 7 (analysis result / summary / appeal letter)
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

# Quantizer for two-decimal-place monetary serialization.
_TWO_PLACES = Decimal("0.01")


def _format_money(value: Optional[Decimal]) -> Optional[str]:
    """Render a Decimal as a two-decimal string (e.g. "12.34"); pass through None."""
    if value is None:
        return None
    return str(value.quantize(_TWO_PLACES))


class SupportedLanguage(str, Enum):
    """Languages the system can produce output in (Supported_Language)."""

    EN = "en"  # English
    ES = "es"  # Spanish
    PT = "pt"  # Portuguese
    HT = "ht"  # Haitian Creole


class LineItem(BaseModel):
    """A single charge entry within an ExtractedBill (Line_Item).

    Optional fields default to ``None`` so absent values are represented as
    null while retaining the populated fields (Req 2.4). Monetary fields are
    non-negative with two decimal places; quantity is non-negative (Req 2.5).
    """

    model_config = ConfigDict(extra="forbid")

    id: str  # stable within a bill, used for references
    description: str
    billing_code: Optional[str] = None
    quantity: Optional[Decimal] = Field(default=None, ge=0)
    unit_price: Optional[Decimal] = Field(default=None, ge=0)
    amount: Optional[Decimal] = Field(default=None, ge=0)

    @field_serializer("quantity", "unit_price", "amount", when_used="json")
    def _serialize_money(self, value: Optional[Decimal]) -> Optional[str]:
        return _format_money(value)


class ExtractedBill(BaseModel):
    """Structured representation of a parsed medical bill (ExtractedBill)."""

    model_config = ConfigDict(extra="forbid")

    provider_name: Optional[str] = None
    provider_address: Optional[str] = None
    account_number: Optional[str] = None
    line_items: list[LineItem] = Field(default_factory=list)
    stated_total: Optional[Decimal] = Field(default=None, ge=0)  # None => Req 3.6 Finding

    @field_serializer("stated_total", when_used="json")
    def _serialize_stated_total(self, value: Optional[Decimal]) -> Optional[str]:
        return _format_money(value)


class FindingType(str, Enum):
    """Categories of issues produced by the Auditor and Rights_Checker."""

    MATH_ERROR = "math_error"
    DUPLICATE_CHARGE = "duplicate_charge"
    SUSPICIOUS_CODE = "suspicious_code"
    RIGHTS_ISSUE = "rights_issue"


class Finding(BaseModel):
    """A single detected issue with evidence and a confidence score.

    ``confidence`` is constrained to the inclusive range [0, 1] (Req 4.6, 5.3).
    Math-error findings may carry both the computed and stated totals (Req 3.2).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    type: FindingType
    description: str
    confidence: Decimal = Field(ge=0, le=1)  # [0, 1] (Req 4.6, 5.3)
    line_item_ids: list[str] = Field(default_factory=list)  # referenced lines
    passage_ids: list[str] = Field(default_factory=list)  # cited KB passages
    computed_total: Optional[Decimal] = None  # for math-error Findings (Req 3.2)
    stated_total: Optional[Decimal] = None

    @field_serializer("computed_total", "stated_total", when_used="json")
    def _serialize_money(self, value: Optional[Decimal]) -> Optional[str]:
        return _format_money(value)


class SumCheckResult(BaseModel):
    """Result of the deterministic Sum_Check (Req 3.1, 3.5)."""

    model_config = ConfigDict(extra="forbid")

    computed_total: Decimal  # 0.00 for empty (Req 3.1)
    excluded_line_item_ids: list[str] = Field(default_factory=list)  # Req 3.5

    @field_serializer("computed_total", when_used="json")
    def _serialize_computed_total(self, value: Decimal) -> str:
        return str(value.quantize(_TWO_PLACES))


class RightsStatus(str, Enum):
    """Status of the Rights_Checker node (Req 5.5)."""

    COMPLETED = "completed"
    UNAVAILABLE = "unavailable"


class Passage(BaseModel):
    """A retrieved reference passage with a normalized relevance score.

    ``relevance_score`` is constrained to the inclusive range [0, 1].
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    source: str
    relevance_score: Decimal = Field(ge=0, le=1)


class AnalysisResult(BaseModel):
    """Final output of the Writer node (Summary + Appeal_Letter)."""

    model_config = ConfigDict(extra="forbid")

    summary_entries: list[str]
    appeal_letter: Optional[str] = None  # None when no Findings (Req 7.4)
    retained_findings: list[Finding]
    language: SupportedLanguage
