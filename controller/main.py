from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from controller.api import chat, health, internal, status
from controller.config import settings
from controller.logging_utils import log_event
from controller.services.lifecycle_manager import lifecycle_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.state_file.parent.mkdir(parents=True, exist_ok=True)
    log_event("controller_started", worker_mode=settings.worker_mode, model=settings.model_id)
    lifecycle_manager.start()
    yield
    await lifecycle_manager.stop()


app = FastAPI(title="Colab GPU Inference Controller", lifespan=lifespan)


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.max_request_body_bytes:
        return JSONResponse(status_code=413, content={"detail": "request body too large"})
    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    log_event("request_received", path=request.url.path, method=request.method)
    return await call_next(request)


app.include_router(health.router)
app.include_router(status.router)
app.include_router(chat.router)
app.include_router(internal.router)
