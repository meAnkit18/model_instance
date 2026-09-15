import os
import tempfile

# Must be set before `controller.config` is first imported anywhere.
os.environ.setdefault("WORKER_MODE", "mock")
os.environ.setdefault("MODEL_ID", "test/dummy-model")
os.environ.setdefault("STATE_FILE", os.path.join(tempfile.mkdtemp(), "state.json"))
os.environ.setdefault("HEARTBEAT_INTERVAL_SECONDS", "1")
os.environ.setdefault("HEARTBEAT_TIMEOUT_SECONDS", "3")
os.environ.setdefault("WORKER_IDLE_TIMEOUT_SECONDS", "5")
os.environ.setdefault("STARTUP_MAX_RETRIES", "2")
os.environ.setdefault("STARTUP_RETRY_BACKOFF_SECONDS", "0")

import pytest


@pytest.fixture(autouse=True)
def _reset_state():
    """Every test gets a clean worker state. `state_store` is a
    module-level singleton imported by name in several modules (`from
    controller.state import state_store`), so tests reset it in place
    rather than swapping the object -- swapping wouldn't be visible to
    modules that already bound the old reference at import time."""
    from controller.state import WorkerStatus, state_store

    state_store._status = WorkerStatus()
    yield state_store
