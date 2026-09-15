"""Background loop per the brief: "Use a background lifecycle manager
rather than relying on incoming requests to perform cleanup." Runs
independently of request traffic so idle shutdown and heartbeat checks
happen even if no one is calling the API."""
from __future__ import annotations

import asyncio

from controller.config import settings
from controller.logging_utils import log_event
from controller.services.worker_manager import worker_manager


class LifecycleManager:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            await self._task

    async def _loop(self) -> None:
        log_event("lifecycle_manager_started")
        while not self._stop_event.is_set():
            try:
                await worker_manager.check_health()
                await worker_manager.stop_if_idle()
            except Exception as e:  # never let a transient error kill the loop
                log_event("lifecycle_loop_error", detail=str(e)[:500])
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=settings.heartbeat_interval_seconds
                )
            except asyncio.TimeoutError:
                pass


lifecycle_manager = LifecycleManager()
