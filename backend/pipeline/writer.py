"""Writer node: Summary in the session language + an English Appeal_Letter (Req 7).

The Writer is the final MVP node. It consumes the list of (retained) Findings
and the session :class:`SupportedLanguage`, and produces an
:class:`AnalysisResult` holding:

* ``summary_entries`` — a plain-language Summary in the SESSION language. With
  >=1 Finding there is EXACTLY one entry per Finding, each identifying a
  distinct Finding and describing its issue (Req 7.1). With zero Findings there
  is a single "no credible issues" entry (Req 7.4).
* ``appeal_letter`` — a single contesting letter with one statement per Finding
  (Req 7.2) when there is >=1 Finding, else ``None`` (Req 7.4). The letter is
  ALWAYS drafted in English regardless of the session language: it is sent to
  Massachusetts billing offices and insurers, which work in English. Patients
  can view a read-only translation via ``POST /sessions/{id}/translate-letter``.
* ``language`` — always the session language (the language of the Summary).

Design "Node Responsibilities / Writer":
    With >=1 retained Finding, produces a Summary with one entry per Finding in
    the session language and an English Appeal_Letter with one contesting
    statement per Finding (Req 7.1, 7.2, 7.3). With zero retained Findings,
    produces a "no credible issues" Summary and no letter (Req 7.4). Generation
    failure returns an error with no partial output (Req 7.5).

Property 11 (one-summary-entry-per-finding) is guaranteed BY CONSTRUCTION, not
left to the LLM: the Summary list is built deterministically with one entry per
Finding (the LLM is used only to phrase each entry and to draft the letter body
within that fixed per-finding structure). The count and one-per-finding
correspondence therefore always hold regardless of LLM nondeterminism.

Error handling is node-fatal (design "Writer | Generation failure |
Node-fatal error; no partial Summary/letter | 7.5"): any provider error,
timeout, or empty/unusable completion raises :class:`WriterError`. No partial
``AnalysisResult`` is ever constructed or returned on failure (Req 7.5).

Security note: this module never reads or references the LLM API key. The
injected provider holds the key; nothing here logs request/response bodies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from models import AnalysisResult, Finding, SupportedLanguage

# Shared node id. Mirrors ``config.NodeName.WRITER`` ("writer"); kept as a plain
# string here so the module imports without a hard dependency on config.py.
NODE_NAME = "writer"

# Req 7: the Writer has no explicit timeout in the spec. A generous bound keeps
# a hung provider from blocking the pipeline indefinitely while leaving ample
# room to draft prose.
WRITER_TIMEOUT_S: float = 60.0

# Human-readable language names used to tag each generation request so the LLM
# drafts prose in the intended language and no other (Req 7.3 / Property 12).
_LANGUAGE_NAMES: dict[SupportedLanguage, str] = {
    SupportedLanguage.EN: "English",
    SupportedLanguage.ES: "Spanish",
    SupportedLanguage.PT: "Portuguese",
    SupportedLanguage.HT: "Haitian Creole",
}

# The appeal letter is always written in English, whatever the session language.
LETTER_LANGUAGE: SupportedLanguage = SupportedLanguage.EN


class LLMProvider(Protocol):
    """Minimal protocol for the injected LLM provider (see design Req 11).

    Mirrors ``llm/provider.py``. Accepting the provider as an argument keeps the
    node testable with a mock and means the Writer never touches credentials.
    """

    def complete(
        self, *, system: str, user: str, timeout_s: float, node: Any
    ) -> str: ...


@dataclass
class WriterError(Exception):
    """Node-fatal error raised by the Writer (Req 7.5).

    Raised for any generation failure — provider error, timeout, or an
    empty/unusable completion. No partial ``AnalysisResult`` is attached:
    callers receive either a complete result or this error, never a partial.
    """

    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message

    def to_state_error(self) -> dict:
        """Shape used for ``PipelineState.error`` ({failedNode, message})."""
        return {"failedNode": NODE_NAME, "message": self.message}


def language_name(language: SupportedLanguage) -> str:
    """Return the display name used to tag a request's language (Req 7.3)."""
    return _LANGUAGE_NAMES.get(language, language.value)


def _finding_label(finding: Finding, index: int) -> str:
    """A short, stable label identifying a Finding within the request."""
    kind = finding.type.value.replace("_", " ")
    return f"Finding {index} ({kind})"


def _summary_system_prompt(language: SupportedLanguage) -> str:
    """System prompt for phrasing a single summary entry in ``language``."""
    name = language_name(language)
    return (
        f"You are a patient-advocate assistant. Write entirely in {name}. "
        "Given one billing-analysis finding, write a single plain-language "
        "sentence a patient can understand that identifies the issue and why it "
        "matters. Return only that sentence, with no preamble, no list markers, "
        "and no markdown."
    )


def _letter_system_prompt(count: int) -> str:
    """System prompt for drafting the appeal letter body, always in English."""
    name = language_name(LETTER_LANGUAGE)
    return (
        f"You are a patient-advocate assistant. Write entirely in {name}. "
        "Draft a polite, professional medical-bill appeal letter that contests "
        f"the billing issues described. Include exactly one contesting "
        f"statement for each of the {count} findings provided, addressing them "
        "in order. Return only the letter text, with no preamble and no "
        "markdown."
    )


def _finding_user_prompt(finding: Finding, index: int) -> str:
    """Render a single Finding into prompt text for summary phrasing.

    Raw reference-passage ids (e.g. ``ma-duplicate-charges``) are deliberately
    left out: rights-issue descriptions already name the passage by its readable
    title, and leaving ids in the prompt invites the LLM to quote them.
    """
    label = _finding_label(finding, index)
    refs = ""
    if finding.line_item_ids:
        refs += f" Line items: {', '.join(finding.line_item_ids)}."
    totals = ""
    if finding.computed_total is not None or finding.stated_total is not None:
        totals = (
            f" Computed total: {finding.computed_total}; "
            f"stated total: {finding.stated_total}."
        )
    return f"{label}: {finding.description}.{refs}{totals}"


def _letter_user_prompt(findings: list[Finding]) -> str:
    """Render all Findings into prompt text for the appeal letter body."""
    lines = [
        _finding_user_prompt(finding, index)
        for index, finding in enumerate(findings, start=1)
    ]
    return "The findings to contest, in order:\n" + "\n".join(lines)


def _require_text(completion: str, what: str) -> str:
    """Return trimmed completion text or raise :class:`WriterError` if empty.

    An empty/whitespace-only completion is a generation failure (Req 7.5).
    """
    text = (completion or "").strip()
    if not text:
        raise WriterError(message=f"Writer failed to generate the {what}.")
    return text


def write_analysis(
    findings: list[Finding],
    language: SupportedLanguage,
    provider: LLMProvider,
) -> AnalysisResult:
    """Run the Writer node (Req 7).

    Branches:

    * **>=1 Finding** — build ``summary_entries`` with exactly one entry per
      Finding (Req 7.1, guaranteed by construction) drafted in ``language``,
      and a single ``appeal_letter`` with one contesting statement per Finding
      (Req 7.2) drafted in English.
    * **zero Findings** — a single "no credible issues" summary entry drafted in
      ``language`` and ``appeal_letter = None`` (Req 7.4).

    Summary requests are tagged with ``language`` and the letter request with
    English; the returned ``AnalysisResult.language`` equals ``language`` (the
    Summary's language, Req 7.3 / Property 12).

    Raises:
        WriterError: on any generation failure (provider error, timeout, or an
            empty/unusable completion). No partial ``AnalysisResult`` is
            produced (Req 7.5).
    """
    if not findings:
        return _write_no_findings(language, provider)
    return _write_with_findings(findings, language, provider)


def _call_provider(provider: LLMProvider, *, system: str, user: str, what: str) -> str:
    """Invoke the provider, turning any failure into a node-fatal WriterError.

    Req 7.5: a provider exception or timeout is a generation failure. We raise
    :class:`WriterError` here before any ``AnalysisResult`` is built, so no
    partial output can escape.
    """
    try:
        completion = provider.complete(
            system=system,
            user=user,
            timeout_s=WRITER_TIMEOUT_S,
            node=NODE_NAME,
        )
    except WriterError:
        raise
    except Exception as exc:  # provider error / timeout => generation failure
        raise WriterError(
            message=f"Writer failed to generate the {what}: {exc}"
        ) from exc
    return _require_text(completion, what)


def translate_letter(
    letter: str,
    target: SupportedLanguage,
    provider: LLMProvider,
) -> str:
    """Translate the English appeal letter into ``target`` for the patient.

    Used only to help the patient understand the letter; the English letter is
    what gets sent. The translation is never stored on the session.

    Raises:
        WriterError: on a provider error/timeout or an empty completion.
    """
    name = language_name(target)
    system = (
        "You are a professional medical-billing translator. Translate the "
        f"following English appeal letter into {name}. Preserve the meaning, "
        "paragraphs, line breaks, names, amounts, dates, and billing codes "
        "exactly. Return only the translated letter, with no preamble, notes, "
        "or markdown."
    )
    return _call_provider(provider, system=system, user=letter, what="translation")


def _write_no_findings(
    language: SupportedLanguage,
    provider: LLMProvider,
) -> AnalysisResult:
    """Zero-Finding branch: one "no credible issues" entry, no letter (Req 7.4)."""
    name = language_name(language)
    system = (
        f"You are a patient-advocate assistant. Write entirely in {name}. "
        "Write a single plain-language sentence telling the patient that the "
        "analysis found no credible billing errors or patient-rights issues on "
        "the bill. Return only that sentence, with no preamble and no markdown."
    )
    user = "No credible billing errors or patient-rights issues were detected."
    entry = _call_provider(
        provider, system=system, user=user, what="summary"
    )
    # Build the complete result only after a successful generation (Req 7.5).
    return AnalysisResult(
        summary_entries=[entry],
        appeal_letter=None,  # Req 7.4: no letter when nothing was found.
        retained_findings=[],
        language=language,  # Req 7.3 / Property 12.
    )


def _write_with_findings(
    findings: list[Finding],
    language: SupportedLanguage,
    provider: LLMProvider,
) -> AnalysisResult:
    """>=1 Finding branch: one summary entry per Finding + one letter (Req 7.1, 7.2, 7.3).

    The Summary is built deterministically with exactly one entry per Finding so
    Property 11 holds by construction; the LLM only phrases each entry, in the
    session language. The letter is drafted once, in English, with one
    contesting statement per Finding. All output
    is generated BEFORE any ``AnalysisResult`` is constructed, so a failure at
    any step yields a :class:`WriterError` and no partial result (Req 7.5).
    """
    summary_system = _summary_system_prompt(language)

    # One entry per Finding — the loop guarantees the count and one-per-finding
    # correspondence (Property 11) regardless of LLM output.
    summary_entries: list[str] = []
    for index, finding in enumerate(findings, start=1):
        entry = _call_provider(
            provider,
            system=summary_system,
            user=_finding_user_prompt(finding, index),
            what="summary",
        )
        summary_entries.append(entry)

    # One English letter with one contesting statement per Finding (Req 7.2).
    letter = _call_provider(
        provider,
        system=_letter_system_prompt(len(findings)),
        user=_letter_user_prompt(findings),
        what="appeal letter",
    )

    # Build the complete result only after every generation succeeded (Req 7.5).
    return AnalysisResult(
        summary_entries=summary_entries,  # len == len(findings) (Property 11).
        appeal_letter=letter,
        retained_findings=list(findings),
        language=language,  # Req 7.3 / Property 12.
    )
