"""Worker->controller push endpoints. Only meaningfully driven by
WORKER_MODE=mock (worker/mock_server.py) -- Colab mode never calls these,
since Render pulls status via colab_driver.py instead (no inbound path
exists to a Colab VM; see docs/research.md section 5). Kept as real,
authenticated endpoints regardless of mode so the brief's worker
registration/heartbeat contract is implemented and testable."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from controller.logging_utils import log_event
from controller.security import verify_worker_secret
from controller.services.registry import worker_registry

router = APIRouter(prefix="/internal/workers")


class RegisterRequest(BaseModel):
    worker_id: str
    status: str
    capabilities: dict = {}


class HeartbeatRequest(BaseModel):
    worker_id: str
    status: str


@router.post("/register")
def register(req: RegisterRequest, _: None = Depends(verify_worker_secret)):
    worker_registry.register(req.worker_id, req.status, req.capabilities)
    log_event("worker_registered", worker_id=req.worker_id, status=req.status)
    return {"ok": True}


@router.post("/heartbeat")
def heartbeat(req: HeartbeatRequest, _: None = Depends(verify_worker_secret)):
    ok = worker_registry.heartbeat(req.worker_id, req.status)
    if not ok:
        log_event("heartbeat_missing", worker_id=req.worker_id, reason="unregistered")
    return {"ok": ok}
