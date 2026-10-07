import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class RateLimiter:
    """Sliding-window limiter (in-memory; swap for Redis when running multiple API replicas)."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def __call__(self, request: Request) -> None:
        host = request.client.host if request.client else "unknown"
        if not self.check(f"{host}:{request.url.path}"):
            raise HTTPException(429, "Too many requests, slow down")


auth_limiter = RateLimiter(int(os.getenv("BUILDBIX_AUTH_RATE_LIMIT", "10")), 60)
