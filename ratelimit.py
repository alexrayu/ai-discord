"""Per-user sliding-window cap on reviews, in memory."""

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(self, limit: int, window: float = 3600, clock: Callable[[], float] = time.monotonic):
        self._limit = limit
        self._window = window
        self._clock = clock
        self._hits: dict[int, deque[float]] = {}

    def acquire(self, user_id: int) -> float:
        """Record a review and return 0, or return the seconds until the next slot frees up."""
        now = self._clock()
        hits = self._hits.setdefault(user_id, deque())
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return hits[0] + self._window - now
        hits.append(now)
        return 0.0
