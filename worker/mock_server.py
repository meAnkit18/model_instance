"""Standalone local worker for WORKER_MODE=mock (docs/architecture.md).

Unlike the Colab path, this process really does run its own HTTP server
and really does push registration/heartbeats to the controller -- there is
no networking constraint on localhost, so this is the literal Component 2
from the original brief, used for local development and the lifecycle
tests that would otherwise require a live Colab GPU for every run.

Run standalone:
    python -m worker.mock_server
The controller spawns this as a subprocess when WORKER_MODE=mock and the
worker is OFFLINE (controller/services/mock_driver.py).
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time
import uuid

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from controller.config import settings
from worker.inference import InferenceEngine

WORKER_ID = os.environ.get("WORKER_ID", f"mock-{uuid.uuid4().hex[:8]}")
CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "http://127.0.0.1:8000")

app = FastAPI(title="mock-colab-worker")
engine = InferenceEngine(
    model_id=settings.model_id,
    revision=settings.model_revision,
    dtype="float32",  # mock worker typically runs on CPU-only dev machines
    max_model_len=settings.max_model_len,
    max_image_pixels=settings.max_image_pixels,
)
_status = "BOOTING"


_consecutive_push_failures = 0
_MAX_PUSH_FAILURES = 5  # self-terminate rather than become an orphaned process


def _push(path: str, body: dict) -> None:
    global _consecutive_push_failures
    headers = {}
    if settings.worker_registration_secret:
        headers["Authorization"] = f"Bearer {settings.worker_registration_secret}"
    try:
        httpx.post(f"{CONTROLLER_URL}{path}", json=body, headers=headers, timeout=10)
        _consecutive_push_failures = 0
    except httpx.HTTPError as e:
        _consecutive_push_failures += 1
        print(f"[mock-worker] failed to push {path}: {e}", file=sys.stderr)
        if _consecutive_push_failures >= _MAX_PUSH_FAILURES:
            print("[mock-worker] controller unreachable, self-terminating", file=sys.stderr)
            os._exit(1)


def _set_status(status: str) -> None:
    global _status
    _status = status
    _push(
        "/internal/workers/register",
        {"worker_id": WORKER_ID, "status": status,
         "capabilities": {"gpu": "mock-cpu", "model": settings.model_id}},
    )


def _heartbeat_loop() -> None:
    while True:
        time.sleep(settings.heartbeat_interval_seconds)
        _push("/internal/workers/heartbeat", {"worker_id": WORKER_ID, "status": _status})


def _boot() -> None:
    _set_status("BOOTING")
    _set_status("LOADING_MODEL")
    try:
        info = engine.load()
    except Exception as e:  # surfaced to controller as MODEL_LOAD_FAILED
        print(f"[mock-worker] model load failed: {e}", file=sys.stderr)
        _set_status("MODEL_LOAD_FAILED")
        return
    print(f"[mock-worker] model ready: {info}")
    _set_status("READY")
    threading.Thread(target=_heartbeat_loop, daemon=True).start()


class GenerateRequest(BaseModel):
    messages: list[dict]
    max_tokens: int = 512
    temperature: float = 0.7


def _check_secret(authorization: str | None) -> None:
    if not settings.worker_registration_secret:
        return
    expected = f"Bearer {settings.worker_registration_secret}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid worker secret")


@app.post("/generate")
def generate(req: GenerateRequest, authorization: str | None = Header(default=None)):
    _check_secret(authorization)
    if _status != "READY":
        raise HTTPException(status_code=503, detail=f"worker not ready (status={_status})")
    return engine.generate(req.messages, max_new_tokens=req.max_tokens, temperature=req.temperature)


@app.get("/health")
def health():
    return {"worker_id": WORKER_ID, "status": _status, **engine.health()}


@app.post("/shutdown")
def shutdown(authorization: str | None = Header(default=None)):
    _check_secret(authorization)
    threading.Thread(target=lambda: (time.sleep(0.2), os.kill(os.getpid(), signal.SIGTERM))).start()
    return {"ok": True}


@app.on_event("startup")
def on_startup():
    threading.Thread(target=_boot, daemon=True).start()


if __name__ == "__main__":
    uvicorn.run(app, host=settings.mock_worker_host, port=settings.mock_worker_port)
