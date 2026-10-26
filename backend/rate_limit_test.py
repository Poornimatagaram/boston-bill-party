"""Tests for cost protection (rate_limit.py) and the deployment settings in config.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import config
import rate_limit
import sessions
from ask_endpoint_test import _call_post_json, _MockProvider, _register_analyzed
from main_test import _MINIMAL_PDF, _upload
from rate_limit import (
    ANALYSES_PER_IP_PER_HOUR,
    DEMO_LIMIT_MESSAGE,
    QUESTIONS_PER_IP_PER_HOUR,
    WINDOW_SECONDS,
    RateLimiter,
)


class FakeClock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


# --------------------------------------------------------------- RateLimiter unit


def test_analysis_limit_per_ip_and_window_slides():
    clock = FakeClock()
    rl = RateLimiter(clock=clock, max_daily=lambda: 1000)
    assert all(rl.allow_analysis("1.1.1.1") for _ in range(ANALYSES_PER_IP_PER_HOUR))
    assert not rl.allow_analysis("1.1.1.1")
    # A different IP has its own window.
    assert rl.allow_analysis("2.2.2.2")
    # After the hour passes, the first IP is allowed again.
    clock.now += WINDOW_SECONDS + 1
    assert rl.allow_analysis("1.1.1.1")


def test_rejected_attempts_do_not_extend_the_window():
    clock = FakeClock()
    rl = RateLimiter(clock=clock, max_daily=lambda: 1000)
    for _ in range(ANALYSES_PER_IP_PER_HOUR):
        rl.allow_analysis("ip")
    clock.now += WINDOW_SECONDS / 2
    assert not rl.allow_analysis("ip")
    clock.now += WINDOW_SECONDS / 2 + 1
    assert rl.allow_analysis("ip")


def test_question_limit_per_ip():
    rl = RateLimiter(clock=FakeClock(), max_daily=lambda: 0)
    assert all(rl.allow_question("ip") for _ in range(QUESTIONS_PER_IP_PER_HOUR))
    assert not rl.allow_question("ip")
    assert rl.allow_question("other")


def test_global_daily_cap_resets_at_utc_midnight():
    # 2027-01-15 23:59:00 UTC
    clock = FakeClock(1_800_057_540.0)
    rl = RateLimiter(clock=clock, max_daily=lambda: 2)
    assert rl.allow_analysis("a")
    assert rl.allow_analysis("b")
    assert not rl.allow_analysis("c")  # cap hit, even for a fresh IP
    clock.now += 120  # past midnight UTC
    assert rl.allow_analysis("c")


def test_zero_daily_cap_blocks_all_analyses():
    rl = RateLimiter(clock=FakeClock(), max_daily=lambda: 0)
    assert not rl.allow_analysis("ip")


# --------------------------------------------------------------- config settings


def test_allowed_origins_default_and_parsing():
    assert config.load_allowed_origins({}) == ["http://localhost:3000"]
    assert config.load_allowed_origins({"ALLOWED_ORIGINS": "  "}) == [
        "http://localhost:3000"
    ]
    assert config.load_allowed_origins(
        {"ALLOWED_ORIGINS": "https://a.example.com/, ,http://localhost:3000"}
    ) == ["https://a.example.com", "http://localhost:3000"]


def test_max_daily_analyses_default_and_validation():
    assert config.load_max_daily_analyses({}) == 50
    assert config.load_max_daily_analyses({"MAX_DAILY_ANALYSES": "7"}) == 7
    for bad in ("-1", "ten", "1.5"):
        with pytest.raises(config.ConfigError) as exc:
            config.load_max_daily_analyses({"MAX_DAILY_ANALYSES": bad})
        assert bad not in str(exc.value)


# --------------------------------------------------------------- endpoints


@pytest.fixture
def mocked_pipeline(monkeypatch):
    monkeypatch.setattr(sessions, "get_provider", lambda: MagicMock())
    monkeypatch.setattr(sessions, "run_pipeline", lambda *a, **k: None)
    sessions.clear_sessions()
    yield
    sessions.clear_sessions()


def _upload_pdf():
    return _upload(data=_MINIMAL_PDF, filename="bill.pdf", content_type="application/pdf")


def test_sixth_analysis_from_same_ip_gets_demo_limit(mocked_pipeline):
    for _ in range(ANALYSES_PER_IP_PER_HOUR):
        status, payload = _upload_pdf()
        assert status == 200, payload
    status, payload = _upload_pdf()
    assert status == 429
    assert payload["detail"] == DEMO_LIMIT_MESSAGE


def test_invalid_uploads_do_not_use_quota(mocked_pipeline):
    for _ in range(ANALYSES_PER_IP_PER_HOUR + 2):
        status, _ = _upload(data=b"x", filename="a.txt", content_type="text/plain")
        assert status == 400
    status, _ = _upload_pdf()
    assert status == 200


def test_daily_cap_from_env(mocked_pipeline, monkeypatch):
    monkeypatch.setenv("MAX_DAILY_ANALYSES", "1")
    assert _upload_pdf()[0] == 200
    status, payload = _upload_pdf()
    assert status == 429
    assert payload["detail"] == DEMO_LIMIT_MESSAGE


def test_forwarded_for_ip_is_used(mocked_pipeline):
    for i in range(ANALYSES_PER_IP_PER_HOUR):
        assert _upload_pdf()[0] == 200
    # A different forwarded client IP has its own quota.
    status, _ = _upload(
        data=_MINIMAL_PDF,
        filename="bill.pdf",
        content_type="application/pdf",
        header=(b"x-forwarded-for", b"203.0.113.9, 10.0.0.1"),
    )
    assert status == 200


def test_question_limit_on_ask_endpoint(monkeypatch):
    sessions.clear_sessions()
    _register_analyzed("ask-limit")
    provider = _MockProvider()
    monkeypatch.setattr(sessions, "get_provider", lambda: provider)
    for _ in range(QUESTIONS_PER_IP_PER_HOUR):
        status, payload = _call_post_json("/sessions/ask-limit/ask", {"question": "Total?"})
        assert status == 200, payload
    status, payload = _call_post_json("/sessions/ask-limit/ask", {"question": "Total?"})
    assert status == 429
    assert payload["detail"] == DEMO_LIMIT_MESSAGE
    assert len(provider.calls) == QUESTIONS_PER_IP_PER_HOUR
    sessions.clear_sessions()
