"""Shared pytest setup for the backend.

Every test starts with empty rate-limit counters, so endpoint tests that all
call from 127.0.0.1 never trip the demo limits on each other.
"""

from __future__ import annotations

import pytest

import rate_limit


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    rate_limit.limiter.reset()
    yield
    rate_limit.limiter.reset()
