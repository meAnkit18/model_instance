"""The worker state machine's data model, persisted to disk so a Render
restart can recover lifecycle metadata (per docs/architecture.md -- the
actual Colab session can outlive the controller process; see
docs/research.md section 4)."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum

from controller.config import settings


class WorkerState(str, Enum):
    OFFLINE = "OFFLINE"
    STARTING = "STARTING"
    BOOTING = "BOOTING"
    LOADING_MODEL = "LOADING_MODEL"
    READY = "READY"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


@dataclass
class WorkerStatus:
    state: WorkerState = WorkerState.OFFLINE
    session_name: str = settings.colab_session_name
    gpu: str | None = None
    model: str | None = None
    last_heartbeat: float | None = None
    last_request: float | None = None
    started_at: float | None = None
    error_message: str | None = None
    consecutive_failures: int = 0
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        return d


class StateStore:
    """Thread-safe holder for the single worker's status, persisted to
    `settings.state_file` after every mutation."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._status = self._load()

    def _load(self) -> WorkerStatus:
        path = settings.state_file
        if path.exists():
            try:
                data = json.loads(path.read_text())
                data["state"] = WorkerState(data["state"])
                return WorkerStatus(**data)
            except Exception:
                # Corrupt or incompatible state file -- start clean rather
                # than crash the controller on boot.
                pass
        return WorkerStatus()

    def _save(self) -> None:
        settings.state_file.parent.mkdir(parents=True, exist_ok=True)
        settings.state_file.write_text(json.dumps(self._status.to_dict(), indent=2))

    def get(self) -> WorkerStatus:
        with self._lock:
            return WorkerStatus(**self._status.to_dict() | {"state": self._status.state})

    def update(self, **kwargs) -> WorkerStatus:
        with self._lock:
            for key, value in kwargs.items():
                setattr(self._status, key, value)
            self._save()
            return self.get()

    def transition(self, new_state: WorkerState, **extra) -> WorkerStatus:
        return self.update(state=new_state, **extra)

    def touch_heartbeat(self) -> None:
        self.update(last_heartbeat=time.time(), consecutive_failures=0)

    def touch_request(self) -> None:
        self.update(last_request=time.time())


state_store = StateStore()
