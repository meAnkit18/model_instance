import time

import pytest
from fastapi import HTTPException

from controller.config import settings
from controller.security import RateLimiter, verify_api_key, verify_worker_secret


def test_verify_api_key_disabled_when_unset():
    settings.api_key = None
    verify_api_key(authorization=None)  # must not raise


def test_verify_api_key_rejects_missing_header():
    settings.api_key = "secret123"
    try:
        with pytest.raises(HTTPException) as exc:
            verify_api_key(authorization=None)
        assert exc.value.status_code == 401
    finally:
        settings.api_key = None


def test_verify_api_key_accepts_correct_bearer():
    settings.api_key = "secret123"
    try:
        verify_api_key(authorization="Bearer secret123")  # must not raise
    finally:
        settings.api_key = None


def test_verify_api_key_rejects_wrong_token():
    settings.api_key = "secret123"
    try:
        with pytest.raises(HTTPException):
            verify_api_key(authorization="Bearer wrong")
    finally:
        settings.api_key = None


def test_verify_worker_secret_rejects_wrong_token():
    settings.worker_registration_secret = "wsecret"
    try:
        with pytest.raises(HTTPException) as exc:
            verify_worker_secret(authorization="Bearer nope")
        assert exc.value.status_code == 401
    finally:
        settings.worker_registration_secret = None


def test_rate_limiter_blocks_after_threshold():
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    for _ in range(3):
        limiter.check("client-a")
    with pytest.raises(HTTPException) as exc:
        limiter.check("client-a")
    assert exc.value.status_code == 429


def test_rate_limiter_is_per_key():
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    limiter.check("client-a")
    limiter.check("client-b")  # different key, independent budget -- must not raise


def test_rate_limiter_window_expires():
    limiter = RateLimiter(max_requests=1, window_seconds=0.05)
    limiter.check("client-a")
    time.sleep(0.1)
    limiter.check("client-a")  # window elapsed -- must not raise
