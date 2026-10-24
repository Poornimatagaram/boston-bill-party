"""Verification tests for POST /sessions/{id}/translate-letter.

Same in-process ASGI approach as ``ask_endpoint_test.py``; the provider is a
monkeypatched mock, so no real LLM is called.
"""

from __future__ import annotations

import pytest

import main
import sessions
from ask_endpoint_test import _call_post_json
from main import MAX_LETTER_CHARS
from models import SupportedLanguage
from pipeline.state import PipelineState
from sessions import Session, SessionStatus


class _MockProvider:
    def __init__(self, reply: str = "Estimado departamento de facturación...") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, timeout_s: float, node) -> str:
        self.calls.append({"system": system, "user": user})
        return self.reply


def _register(session_id: str, language: SupportedLanguage) -> None:
    state = PipelineState(session_id=session_id, language=language)
    sessions._SESSIONS[session_id] = Session(
        id=session_id, language=language, state=state, status=SessionStatus.COMPLETED
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    sessions.clear_sessions()
    yield
    sessions.clear_sessions()


def test_unknown_session_returns_404(monkeypatch):
    monkeypatch.setattr(sessions, "get_provider", lambda: _MockProvider())
    status, payload = _call_post_json("/sessions/nope/translate-letter", {"letter": "Hi"})
    assert status == 404


def test_english_session_needs_no_translation(monkeypatch):
    _register("tr-en", SupportedLanguage.EN)
    provider = _MockProvider()
    monkeypatch.setattr(sessions, "get_provider", lambda: provider)
    status, payload = _call_post_json("/sessions/tr-en/translate-letter", {"letter": "Hi"})
    assert status == 400
    assert "english" in payload["detail"].lower()
    assert provider.calls == []


@pytest.mark.parametrize("letter", ["", "   "])
def test_empty_letter_rejected(monkeypatch, letter):
    _register("tr-empty", SupportedLanguage.ES)
    monkeypatch.setattr(sessions, "get_provider", lambda: _MockProvider())
    status, _ = _call_post_json("/sessions/tr-empty/translate-letter", {"letter": letter})
    assert status == 400


def test_too_long_letter_rejected(monkeypatch):
    _register("tr-long", SupportedLanguage.ES)
    monkeypatch.setattr(sessions, "get_provider", lambda: _MockProvider())
    status, payload = _call_post_json(
        "/sessions/tr-long/translate-letter", {"letter": "a" * (MAX_LETTER_CHARS + 1)}
    )
    assert status == 400
    assert str(MAX_LETTER_CHARS) in payload["detail"]


def test_translates_into_session_language(monkeypatch):
    _register("tr-ok", SupportedLanguage.PT)
    provider = _MockProvider(reply="Prezado setor de cobrança...")
    monkeypatch.setattr(sessions, "get_provider", lambda: provider)

    status, payload = _call_post_json(
        "/sessions/tr-ok/translate-letter", {"letter": "  Dear billing office...  "}
    )
    assert status == 200, payload
    assert payload == {"translation": "Prezado setor de cobrança...", "language": "pt"}
    assert "into Portuguese" in provider.calls[0]["system"]
    assert provider.calls[0]["user"] == "Dear billing office..."
    # Read-only: nothing is stored on the session.
    assert sessions.get_session("tr-ok").confirmed_letter is None


def test_provider_failure_returns_502(monkeypatch):
    _register("tr-fail", SupportedLanguage.HT)

    class _Boom:
        def complete(self, **kwargs):
            raise RuntimeError("secret provider detail")

    monkeypatch.setattr(sessions, "get_provider", lambda: _Boom())
    status, payload = _call_post_json("/sessions/tr-fail/translate-letter", {"letter": "Hi"})
    assert status == 502
    assert "secret" not in payload["detail"]


def test_provider_timeout_returns_504(monkeypatch):
    _register("tr-slow", SupportedLanguage.ES)

    class _Hang:
        def complete(self, **kwargs):
            import time

            time.sleep(3)
            return "too late"

    monkeypatch.setattr(sessions, "get_provider", lambda: _Hang())
    monkeypatch.setattr(main, "TRANSLATE_TIMEOUT_SECONDS", 0.2)
    status, _ = _call_post_json("/sessions/tr-slow/translate-letter", {"letter": "Hi"})
    assert status == 504
