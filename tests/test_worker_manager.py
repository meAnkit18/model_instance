"""Controller-level lifecycle tests, per the brief's Testing section:
worker-already-ready, worker-offline, startup lock, concurrent requests,
startup timeout/GPU unavailable, heartbeat timeout, idle timeout, worker
restart. Uses a FakeDriver so these never touch the real `colab` CLI."""
import asyncio
import time

import pytest

from controller.services.colab_manager import GpuUnavailableError
from controller.services.worker_manager import WorkerManager, WorkerUnavailable
from controller.state import WorkerState, state_store


class FakeDriver:
    def __init__(self, start_delay=0.0, fail_times=0, gpu_unavailable_times=0):
        self.start_calls = 0
        self.infer_calls = 0
        self.stop_calls = 0
        self.start_delay = start_delay
        self.fail_times = fail_times
        self.gpu_unavailable_times = gpu_unavailable_times
        self.healthy = True

    def start(self):
        self.start_calls += 1
        if self.start_delay:
            time.sleep(self.start_delay)
        if self.gpu_unavailable_times > 0:
            self.gpu_unavailable_times -= 1
            raise GpuUnavailableError("no gpu", 1, "", "")
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("boom")
        return {"gpu_name": "FakeGPU", "device": "cuda"}

    def infer(self, request):
        self.infer_calls += 1
        return {"text": "ok", "prompt_tokens": 1, "completion_tokens": 1}

    def health(self):
        return {"status": "READY"} if self.healthy else None

    def stop(self):
        self.stop_calls += 1


def make_manager(driver):
    wm = WorkerManager.__new__(WorkerManager)
    wm.driver = driver
    wm._startup_lock = asyncio.Lock()
    wm._startup_task = None
    wm._infer_semaphore = asyncio.Semaphore(4)
    wm._error_at = None
    return wm


@pytest.mark.asyncio
async def test_worker_already_ready_skips_start():
    driver = FakeDriver()
    wm = make_manager(driver)
    state_store.transition(WorkerState.READY, last_request=None, started_at=time.time())

    result = await wm.run_inference({"messages": []})

    assert result["text"] == "ok"
    assert driver.start_calls == 0  # already READY -- no provisioning triggered


@pytest.mark.asyncio
async def test_worker_offline_triggers_start_then_infers():
    driver = FakeDriver()
    wm = make_manager(driver)
    assert state_store.get().state == WorkerState.OFFLINE

    result = await wm.run_inference({"messages": []})

    assert result["text"] == "ok"
    assert driver.start_calls == 1
    assert state_store.get().state == WorkerState.READY


@pytest.mark.asyncio
async def test_concurrent_requests_start_worker_exactly_once():
    driver = FakeDriver(start_delay=0.2)
    wm = make_manager(driver)

    results = await asyncio.gather(*[wm.run_inference({"messages": []}) for _ in range(20)])

    assert len(results) == 20
    assert all(r["text"] == "ok" for r in results)
    assert driver.start_calls == 1  # the "20 requests -> one STARTING op" requirement
    assert driver.infer_calls == 20


@pytest.mark.asyncio
async def test_gpu_unavailable_retries_then_raises():
    driver = FakeDriver(gpu_unavailable_times=99)  # always unavailable
    wm = make_manager(driver)

    with pytest.raises(WorkerUnavailable):
        await wm.run_inference({"messages": []})

    assert state_store.get().state == WorkerState.ERROR
    assert state_store.get().error_message == "GPU_UNAVAILABLE"
    # bounded retries, not infinite -- see .env STARTUP_MAX_RETRIES=2 in conftest
    from controller.config import settings
    assert driver.start_calls == settings.startup_max_retries
    # Regression: a real bug found via live deployment testing
    # (docs/research.md section 11) -- cleanup only ran BETWEEN retries,
    # so the final exhausted attempt left a GPU session running
    # indefinitely with nothing left to stop it. Every failed attempt,
    # including the last, must be cleaned up.
    assert driver.stop_calls == settings.startup_max_retries


@pytest.mark.asyncio
async def test_startup_retries_recover_after_transient_failure():
    driver = FakeDriver(fail_times=1)  # first attempt fails, second succeeds
    wm = make_manager(driver)

    result = await wm.run_inference({"messages": []})

    assert result["text"] == "ok"
    assert driver.start_calls == 2
    assert driver.stop_calls == 1  # cleanup before the retry, per docs/research.md section 10
    assert state_store.get().state == WorkerState.READY


@pytest.mark.asyncio
async def test_heartbeat_timeout_marks_worker_error():
    driver = FakeDriver()
    driver.healthy = False
    wm = make_manager(driver)
    state_store.transition(
        WorkerState.READY,
        last_heartbeat=time.time() - 1000,  # long past HEARTBEAT_TIMEOUT_SECONDS
        started_at=time.time(),
    )

    await wm.check_health()

    assert state_store.get().state == WorkerState.ERROR
    assert state_store.get().error_message == "HEARTBEAT_TIMEOUT"


@pytest.mark.asyncio
async def test_idle_timeout_stops_worker():
    driver = FakeDriver()
    wm = make_manager(driver)
    from controller.config import settings
    state_store.transition(
        WorkerState.READY,
        last_request=time.time() - settings.worker_idle_timeout_seconds - 1,
        started_at=time.time() - settings.worker_idle_timeout_seconds - 1,
    )

    await wm.stop_if_idle()

    assert driver.stop_calls == 1
    assert state_store.get().state == WorkerState.OFFLINE


@pytest.mark.asyncio
async def test_error_cooldown_prevents_retry_storm():
    """After exhausting retries, a fresh request during the backoff
    window should fail fast instead of hammering `colab new` again."""
    from controller.config import settings
    driver = FakeDriver(gpu_unavailable_times=99)
    wm = make_manager(driver)
    settings.startup_retry_backoff_seconds = 60  # long cooldown for this test only
    try:
        with pytest.raises(WorkerUnavailable):
            await wm.run_inference({"messages": []})
        calls_after_first_failure = driver.start_calls

        with pytest.raises(WorkerUnavailable):
            await wm.run_inference({"messages": []})

        assert driver.start_calls == calls_after_first_failure  # no new attempt during cooldown
    finally:
        settings.startup_retry_backoff_seconds = 0


@pytest.mark.asyncio
async def test_worker_restarts_after_going_offline():
    """The brief's success criterion 12: a later request after idle
    shutdown repeats the whole startup process automatically."""
    driver = FakeDriver()
    wm = make_manager(driver)
    await wm.run_inference({"messages": []})
    await wm._stop()
    assert state_store.get().state == WorkerState.OFFLINE

    result = await wm.run_inference({"messages": []})

    assert result["text"] == "ok"
    assert driver.start_calls == 2
