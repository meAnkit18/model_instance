"""WORKER_MODE=mock driver: spawns worker/mock_server.py as a local
subprocess and talks to it exactly like a real Colab worker would talk to
Render in the original (pre-research) design -- push-based registration
and heartbeats, direct HTTP calls for inference. See docs/architecture.md
for why Colab mode instead uses colab_driver.py's pull-based approach."""
from __future__ import annotations

import subprocess
import sys
import time
import uuid

import httpx

from controller.config import settings
from controller.services.registry import worker_registry


class ModelLoadFailed(RuntimeError):
    pass


class MockWorkerDriver:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._worker_id = f"mock-{uuid.uuid4().hex[:8]}"

    @property
    def base_url(self) -> str:
        return f"http://{settings.mock_worker_host}:{settings.mock_worker_port}"

    def start(self) -> dict:
        worker_registry.clear()
        import os
        env = dict(os.environ)
        env["WORKER_ID"] = self._worker_id
        env["CONTROLLER_URL"] = f"http://{settings.mock_worker_host}:8000"
        log_path = settings.state_file.parent / f"mock_worker_{self._worker_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "w")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "worker.mock_server"],
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )

        deadline = time.monotonic() + settings.startup_timeout_seconds
        while time.monotonic() < deadline:
            push = worker_registry.current()
            if push and push.worker_id == self._worker_id:
                if push.status == "READY":
                    return {"model": settings.model_id, "device": "mock-cpu"}
                if push.status == "MODEL_LOAD_FAILED":
                    raise ModelLoadFailed("mock worker reported MODEL_LOAD_FAILED")
            if self._proc.poll() is not None:
                raise ModelLoadFailed(
                    f"mock worker process exited early (code {self._proc.returncode})"
                )
            time.sleep(0.5)
        raise TimeoutError("mock worker did not become READY in time")

    def infer(self, request: dict) -> dict:
        headers = {}
        if settings.worker_registration_secret:
            headers["Authorization"] = f"Bearer {settings.worker_registration_secret}"
        resp = httpx.post(
            f"{self.base_url}/generate", json=request, headers=headers,
            timeout=settings.inference_timeout_seconds,
        )
        resp.raise_for_status()
        return resp.json()

    def health(self) -> dict | None:
        push = worker_registry.current()
        if push is None or push.worker_id != self._worker_id:
            return None
        return {"status": push.status, "last_seen": push.last_seen}

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None
        worker_registry.clear()
