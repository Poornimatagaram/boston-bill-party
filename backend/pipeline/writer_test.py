"""Writer language behavior: Summary in the session language, letter in English.

A recording stub provider captures every system prompt so we can check which
language each generation request was tagged with. No network or real LLM.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from models import Finding, FindingType, SupportedLanguage
from pipeline.writer import WriterError, translate_letter, write_analysis


class _RecordingProvider:
    def __init__(self, reply: str = "Generated text.") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, timeout_s: float, node) -> str:
        self.calls.append({"system": system, "user": user, "node": node})
        return self.reply


def _findings() -> list[Finding]:
    return [
        Finding(
            id="dup:l1",
            type=FindingType.DUPLICATE_CHARGE,
            description="The consultation is billed twice.",
            confidence=Decimal("0.8"),
            line_item_ids=["l1", "l2"],
        ),
        Finding(
            id="rights_issue:ma-duplicate-charges",
            type=FindingType.RIGHTS_ISSUE,
            description="Supported by “Protection from duplicate charges”.",
            confidence=Decimal("0.75"),
            passage_ids=["ma-duplicate-charges"],
        ),
    ]


@pytest.mark.parametrize(
    ("language", "name"),
    [
        (SupportedLanguage.ES, "Spanish"),
        (SupportedLanguage.PT, "Portuguese"),
        (SupportedLanguage.HT, "Haitian Creole"),
    ],
)
def test_letter_is_english_while_summary_uses_session_language(language, name):
    provider = _RecordingProvider()
    result = write_analysis(_findings(), language, provider)

    # One summary call per finding, then exactly one letter call.
    summary_calls, letter_call = provider.calls[:-1], provider.calls[-1]
    assert len(summary_calls) == 2
    for call in summary_calls:
        assert f"Write entirely in {name}" in call["system"]
    assert "Write entirely in English" in letter_call["system"]
    assert name not in letter_call["system"]

    # The result is still tagged with the session (Summary) language.
    assert result.language == language
    assert result.appeal_letter == "Generated text."
    assert len(result.summary_entries) == 2


def test_english_session_writes_everything_in_english():
    provider = _RecordingProvider()
    result = write_analysis(_findings(), SupportedLanguage.EN, provider)
    assert all("Write entirely in English" in c["system"] for c in provider.calls)
    assert result.language == SupportedLanguage.EN


def test_raw_passage_ids_are_not_sent_to_the_writer():
    provider = _RecordingProvider()
    write_analysis(_findings(), SupportedLanguage.ES, provider)
    assert all("ma-duplicate-charges" not in c["user"] for c in provider.calls)


def test_translate_letter_targets_session_language():
    provider = _RecordingProvider(reply="Carta traducida.")
    out = translate_letter("Dear billing office...", SupportedLanguage.ES, provider)
    assert out == "Carta traducida."
    call = provider.calls[0]
    assert "into Spanish" in call["system"]
    assert call["user"] == "Dear billing office..."


def test_translate_letter_empty_completion_is_an_error():
    with pytest.raises(WriterError):
        translate_letter("Dear billing office...", SupportedLanguage.PT, _RecordingProvider(reply="  "))
