"""A small in-memory fixed-window rate limiter for login hardening.

v1 is single-process and in-memory — adequate for one Render web service. A
distributed store (Redis) is the documented next step if the backend ever scales
to multiple instances; this keeps a clean seam (one `hit()` call) for that swap.
"""

from __future__ import annotations

import time
from collections import defaultdict

from app.errors import RateLimitError


class FixedWindowRateLimiter:
    """Allow at most `max_hits` per `window_seconds` per key (e.g. client IP)."""

    def __init__(self, max_hits: int, window_seconds: float):
        self.max_hits = max_hits
        self.window = window_seconds
        # key -> (window_start_monotonic, count)
        self._buckets: dict[str, tuple[float, int]] = defaultdict(lambda: (0.0, 0))

    def hit(self, key: str) -> None:
        """Record one attempt for `key`; raise RateLimitError if over the limit."""
        now = time.monotonic()
        start, count = self._buckets[key]
        if now - start >= self.window:
            # New window.
            self._buckets[key] = (now, 1)
            return
        if count >= self.max_hits:
            raise RateLimitError(
                "Too many attempts. Please wait a moment and try again.",
            )
        self._buckets[key] = (start, count + 1)


# Login: 5 attempts per minute per IP. Generous enough for typos, tight enough
# to blunt credential-stuffing.
login_rate_limiter = FixedWindowRateLimiter(max_hits=5, window_seconds=60.0)
