import time

from fastapi import APIRouter, Depends

from controller.config import settings
from controller.security import verify_api_key
from controller.state import state_store

router = APIRouter()


@router.get("/status")
def status(_: None = Depends(verify_api_key)):
    s = state_store.get()
    uptime = None
    if s.started_at:
        uptime = round(time.time() - s.started_at)
    return {
        "controller": "healthy",
        "worker_mode": settings.worker_mode,
        "worker": {
            "state": s.state.value,
            "session_name": s.session_name,
            "model": s.model,
            "gpu": s.gpu,
            "last_heartbeat": s.last_heartbeat,
            "last_request": s.last_request,
            "uptime_seconds": uptime,
            "error_message": s.error_message,
        },
    }
