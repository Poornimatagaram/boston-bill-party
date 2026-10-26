"""In-memory cost protection for the public demo.

Two kinds of limits guard LLM spend:

* Per-IP sliding windows: 5 bill analyses, 20 chat questions, and 10 letter
  translations per hour.
* A global cap on bill analyses per UTC day (``MAX_DAILY_ANALYSES``, default 50).

State lives in process memory, like sessions. It resets on restart and is not
shared across workers, which fits a single-instance demo deployment. Client IPs
come from ``X-Forwarded-For`` (set by Render's proxy), which a client can spoof;
the global daily cap is the hard backstop.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import HTTPException, Request

import config

WINDOW_SECONDS = 60 * 60
ANALYSES_PER_IP_PER_HOUR = 5
QUESTIONS_PER_IP_PER_HOUR = 20
TRANSLATIONS_PER_IP_PER_HOUR = 10

#: Shown to the user when any limit is hit (status 429).
DEMO_LIMIT_MESSAGE = (
    "Demo limit reached. This free demo has a usage limit to keep costs down. "
    "Please try again later."
)


class RateLimiter:
    """Thread-safe sliding-window counters plus a daily global analysis cap."""

    def __init__(
        self,
        clock: Callable[[], float] = time.time,
        max_daily: Optional[Callable[[], int]] = None,
    ) -> None:
        self._clock = clock
        self._max_daily = max_daily or config.load_max_daily_analyses
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._day: Optional[str] = None
        self._day_count = 0

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._day = None
            self._day_count = 0

    def _window_allows(self, key: tuple[str, str], limit: int, now: float) -> bool:
        hits = self._hits[key]
        while hits and hits[0] <= now - WINDOW_SECONDS:
            hits.popleft()
        return len(hits) < limit

    def _today(self, now: float) -> str:
        return datetime.fromtimestamp(now, tz=timezone.utc).date().isoformat()

    def allow_analysis(self, ip: str) -> bool:
        """Record and allow one analysis, or return False if a limit is hit."""
        now = self._clock()
        cap = self._max_daily()
        with self._lock:
            today = self._today(now)
            if self._day != today:
                self._day, self._day_count = today, 0
            key = ("analysis", ip)
            if self._day_count >= cap:
                return False
            if not self._window_allows(key, ANALYSES_PER_IP_PER_HOUR, now):
                return False
            self._hits[key].append(now)
            self._day_count += 1
            return True

    def _allow_windowed(self, kind: str, ip: str, limit: int) -> bool:
        now = self._clock()
        with self._lock:
            key = (kind, ip)
            if not self._window_allows(key, limit, now):
                return False
            self._hits[key].append(now)
            return True

    def allow_question(self, ip: str) -> bool:
        """Record and allow one chat question, or return False if limited."""
        return self._allow_windowed("question", ip, QUESTIONS_PER_IP_PER_HOUR)

    def allow_translation(self, ip: str) -> bool:
        """Record and allow one letter translation, or return False if limited."""
        return self._allow_windowed("translation", ip, TRANSLATIONS_PER_IP_PER_HOUR)


#: Process-wide limiter used by the HTTP routes.
limiter = RateLimiter()


def client_ip(request: Request) -> str:
    """Best-effort client IP: first ``X-Forwarded-For`` entry, else the peer."""
    forwarded = request.headers.get("x-forwarded-for", "")
    first = forwarded.split(",")[0].strip()
    if first:
        return first
    return request.client.host if request.client else "unknown"


def demo_limit_error() -> HTTPException:
    return HTTPException(status_code=429, detail=DEMO_LIMIT_MESSAGE)
