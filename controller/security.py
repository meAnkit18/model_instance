"""Auth, request validation, and rate limiting for the public and internal
APIs. Kept deliberately simple (in-memory) per the brief's "do not
overengineer this" principle -- a single Render instance doesn't need a
distributed rate limiter."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from controller.config import settings


def verify_api_key(authorization: str | None = Header(default=None)) -> None:
    if not settings.api_key:
        return  # auth disabled (e.g. local dev)
    expected = f"Bearer {settings.api_key}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def verify_worker_secret(authorization: str | None = Header(default=None)) -> None:
    if not settings.worker_registration_secret:
        return
    expected = f"Bearer {settings.worker_registration_secret}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid or missing worker secret")


class RateLimiter:
    """Fixed-window-ish limiter keyed by client IP. Not distributed --
    fine for a single Render instance; documented as a known limit if the
    service is ever scaled to multiple instances."""

    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.time()
        q = self._hits[key]
        while q and now - q[0] > self.window_seconds:
            q.popleft()
        if len(q) >= self.max_requests:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        q.append(now)


inference_rate_limiter = RateLimiter(max_requests=60, window_seconds=60.0)


def rate_limit_inference(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    inference_rate_limiter.check(client)
