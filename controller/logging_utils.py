"""Structured logging with secret redaction.

Every lifecycle event listed in the brief (request_received, worker_missing,
startup_requested, ...) is logged through `log_event` so operations stay
traceable. `redact` is applied to anything that might contain a token
before it is ever logged, per the brief's "no credentials in logs/errors"
requirement.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import time

logger = logging.getLogger("controller")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

# Matches long opaque tokens (OAuth bearer tokens, API keys) so they never
# make it into a log line even if a subprocess's stdout/stderr echoes one.
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\-\.]{24,}")


def redact(text: str) -> str:
    if not text:
        return text
    return _TOKEN_PATTERN.sub(lambda m: m.group(0)[:4] + "…redacted…", text)


def log_event(event: str, **fields) -> None:
    payload = {"ts": time.time(), "event": event}
    for key, value in fields.items():
        if isinstance(value, str):
            value = redact(value)
        payload[key] = value
    logger.info(json.dumps(payload, default=str))
