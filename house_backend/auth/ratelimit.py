"""A small in-process rate limiter for the auth routes, per client address.

One per :class:`~house_backend.auth.HouseAuth`: each app (and each test app)
has its own table of hits. Memory-only is enough for one process on one host.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[datetime]] = defaultdict(deque)

    def allow(self, key: str, limit_per_minute: int) -> bool:
        """True if the call is allowed; False if ``key`` is over its budget."""
        now = datetime.now(tz=UTC)
        cutoff = now - timedelta(minutes=1)
        bucket = self._hits[key]
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= limit_per_minute:
            return False
        bucket.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()
