"""The state machine from docs/architecture.md: OFFLINE -> STARTING ->
BOOTING -> LOADING_MODEL -> READY -> (idle) STOPPING -> OFFLINE, with a
shared-future startup lock so N concurrent requests trigger exactly one
provisioning operation (the brief's "20 requests -> one STARTING op").

Dispatches to ColabWorkerDriver or MockWorkerDriver based on
settings.worker_mode -- this is the only place that branches on mode.
"""
from __future__ import annotations

import asyncio
import time

from controller.config import settings
from controller.logging_utils import log_event
from controller.services.colab_driver import ColabWorkerDriver
from controller.services.colab_manager import GpuUnavailableError
from controller.services.mock_driver import MockWorkerDriver
from controller.state import WorkerState, state_store


class WorkerUnavailable(RuntimeError):
    pass


class WorkerManager:
    def __init__(self) -> None:
        self.driver = ColabWorkerDriver() if settings.worker_mode == "colab" else MockWorkerDriver()
        self._startup_lock = asyncio.Lock()
        self._startup_task: asyncio.Task | None = None
        self._infer_semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        self._error_at: float | None = None

    # ---- public API -------------------------------------------------

    async def ensure_ready(self):
        status = state_store.get()
        if status.state == WorkerState.READY:
            return status

        if status.state == WorkerState.ERROR and self._error_at is not None:
            cooldown_left = settings.startup_retry_backoff_seconds - (time.time() - self._error_at)
            if cooldown_left > 0:
                raise WorkerUnavailable(
                    f"worker in ERROR state, retrying in {cooldown_left:.0f}s"
                )

        async with self._startup_lock:
            status = state_store.get()
            if status.state == WorkerState.READY:
                return status
            if self._startup_task is None or self._startup_task.done():
                self._startup_task = asyncio.create_task(self._do_startup())
            task = self._startup_task

        result = await task
        if result.state != WorkerState.READY:
            raise WorkerUnavailable(result.error_message or "worker failed to start")
        return result

    async def run_inference(self, request: dict) -> dict:
        await self.ensure_ready()
        async with self._infer_semaphore:
            state_store.touch_request()
            log_event("inference_started")
            t0 = time.monotonic()
            try:
                result = await asyncio.to_thread(self.driver.infer, request)
            except Exception as e:
                log_event("inference_error", detail=str(e)[:500])
                raise
            log_event("inference_completed", latency_seconds=round(time.monotonic() - t0, 2))
            return result

    async def check_health(self) -> None:
        """Called periodically by the lifecycle manager. Pull-based for
        Colab (a successful `colab status` IS the heartbeat); reads
        pushed-heartbeat state for mock."""
        status = state_store.get()
        if status.state != WorkerState.READY:
            return
        info = await asyncio.to_thread(self.driver.health)
        if info is None:
            log_event("heartbeat_missing", session=status.session_name)
        elif "last_seen" in info:
            state_store.update(last_heartbeat=info["last_seen"])
        else:
            state_store.touch_heartbeat()

        fresh = state_store.get()
        if fresh.last_heartbeat and (time.time() - fresh.last_heartbeat) > settings.heartbeat_timeout_seconds:
            log_event("worker_unhealthy", last_heartbeat=fresh.last_heartbeat)
            self._error_at = time.time()
            state_store.transition(WorkerState.ERROR, error_message="HEARTBEAT_TIMEOUT")

    async def stop_if_idle(self) -> None:
        status = state_store.get()
        if status.state != WorkerState.READY:
            return
        idle_since = max(status.last_request or 0, status.started_at or 0)
        if idle_since and (time.time() - idle_since) >= settings.worker_idle_timeout_seconds:
            log_event("idle_timeout", session=status.session_name, idle_since=idle_since)
            await self._stop()

    async def force_stop(self) -> None:
        await self._stop()

    # ---- internals ----------------------------------------------------

    async def _do_startup(self):
        log_event("startup_requested")
        state_store.transition(WorkerState.STARTING, error_message=None)
        for attempt in range(1, settings.startup_max_retries + 1):
            try:
                state_store.transition(WorkerState.BOOTING)
                log_event("worker_boot_started", attempt=attempt)
                state_store.transition(WorkerState.LOADING_MODEL)
                log_event("model_loading", model=settings.model_id)
                ready_info = await asyncio.to_thread(self.driver.start)
                state_store.transition(
                    WorkerState.READY,
                    gpu=ready_info.get("gpu_name") or ready_info.get("device") or settings.colab_gpu,
                    model=settings.model_id,
                    started_at=time.time(),
                    last_heartbeat=time.time(),
                    metrics=ready_info,
                    consecutive_failures=0,
                    error_message=None,
                )
                self._error_at = None
                log_event("worker_registered", info=ready_info)
                log_event("model_ready", info=ready_info)
                return state_store.get()
            except GpuUnavailableError as e:
                log_event(
                    "startup_failed", reason="gpu_unavailable", attempt=attempt,
                    detail=str(e), stdout=getattr(e, "stdout", ""), stderr=getattr(e, "stderr", ""),
                )
                self._error_at = time.time()
                state_store.transition(WorkerState.ERROR, error_message="GPU_UNAVAILABLE")
            except Exception as e:
                log_event(
                    "startup_failed", reason="error", attempt=attempt,
                    detail=str(e)[:500], stdout=getattr(e, "stdout", "")[-2000:],
                    stderr=getattr(e, "stderr", "")[-2000:],
                )
                self._error_at = time.time()
                state_store.transition(WorkerState.ERROR, error_message=str(e)[:500])

            # Best-effort teardown after EVERY failed attempt, including the
            # last one -- not just before a retry. A second real bug found
            # via live deployment testing (docs/research.md section 11):
            # cleanup previously only ran between retries, so an attempt
            # that failed on its final try left a partially-started GPU
            # session running indefinitely, burning quota with nothing
            # left to stop it (the session name is only resolvable by the
            # same CLI-host process that created it, so it couldn't even
            # be cleaned up from elsewhere after the fact).
            try:
                await asyncio.to_thread(self.driver.stop)
            except Exception as cleanup_err:
                log_event("startup_cleanup_failed", detail=str(cleanup_err)[:300])

            if attempt < settings.startup_max_retries:
                await asyncio.sleep(settings.startup_retry_backoff_seconds)
                state_store.transition(WorkerState.STARTING)
        return state_store.get()

    async def _stop(self) -> None:
        state_store.transition(WorkerState.STOPPING)
        log_event("shutdown_requested", session=state_store.get().session_name)
        try:
            await asyncio.to_thread(self.driver.stop)
        finally:
            state_store.transition(
                WorkerState.OFFLINE,
                error_message=None,
                last_heartbeat=None,
                started_at=None,
            )
            log_event(
                "colab_runtime_stopped" if settings.worker_mode == "colab" else "worker_stopped"
            )


worker_manager = WorkerManager()
