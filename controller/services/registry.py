"""In-memory record of the most recent push from a worker, used only by
WORKER_MODE=mock (docs/architecture.md -- Colab mode never pushes; Render
pulls via colab_driver.py instead). Backs POST /internal/workers/register
and POST /internal/workers/heartbeat."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class WorkerPush:
    worker_id: str
    status: str
    capabilities: dict = field(default_factory=dict)
    last_seen: float = field(default_factory=time.time)


class WorkerRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: WorkerPush | None = None

    def register(self, worker_id: str, status: str, capabilities: dict) -> None:
        with self._lock:
            self._last = WorkerPush(worker_id=worker_id, status=status, capabilities=capabilities)

    def heartbeat(self, worker_id: str, status: str) -> bool:
        with self._lock:
            if self._last is None or self._last.worker_id != worker_id:
                return False
            self._last.status = status
            self._last.last_seen = time.time()
            return True

    def current(self) -> WorkerPush | None:
        with self._lock:
            return self._last

    def clear(self) -> None:
        with self._lock:
            self._last = None


worker_registry = WorkerRegistry()
