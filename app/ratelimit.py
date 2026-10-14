"""Spend and abuse controls.

Two layers, because they protect different things:
  * SlidingWindowLimiter per client address - stops one client hammering us.
  * GuardedTriageClient - a global ceiling on AI calls per hour, so total API
    spend is bounded no matter how many addresses are involved.

State is in process memory. That's fine for a single-process deployment; with
several workers each would enforce its own limit (see README limitations).
"""

import threading
import time
from collections import deque

from .ai_client import AnalysisError, TriageClient
from .schemas import TicketInput, TriageResult


class SlidingWindowLimiter:
    """Allow at most `limit` events per `window_seconds` for each key."""

    def __init__(self, limit: int, window_seconds: float, clock=time.monotonic, max_keys: int = 10_000):
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self._max_keys:
                    self._evict(now)
                hits = self._hits[key] = deque()
            while hits and now - hits[0] >= self._window:
                hits.popleft()
            if len(hits) >= self._limit:
                return False
            hits.append(now)
            return True

    def _evict(self, now: float) -> None:
        """Bound memory: drop idle keys, and the oldest key if still full."""
        for key in [k for k, h in self._hits.items() if not h or now - h[-1] >= self._window]:
            del self._hits[key]
        if len(self._hits) >= self._max_keys:
            del self._hits[next(iter(self._hits))]


class GuardedTriageClient:
    """Wraps a TriageClient with a global calls-per-hour ceiling.

    When the ceiling is hit, analysis fails with the ordinary `rate_limited`
    code: the ticket is still saved and can be retried later.
    """

    def __init__(self, inner: TriageClient, max_calls_per_hour: int, clock=time.monotonic):
        self._inner = inner
        self._limiter = SlidingWindowLimiter(max_calls_per_hour, 3600, clock=clock)

    @property
    def model(self) -> str:
        return self._inner.model

    def analyze(self, ticket: TicketInput) -> TriageResult:
        if not self._limiter.allow("global"):
            raise AnalysisError("rate_limited")
        return self._inner.analyze(ticket)
