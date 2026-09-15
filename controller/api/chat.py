"""OpenAI-compatible surface. The client never needs to know whether the
worker is READY, STARTING, or being restarted -- run_inference() hides
that behind a single await (the brief's "abstract that away" requirement)."""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from controller.config import settings
from controller.security import rate_limit_inference, verify_api_key
from controller.services.worker_manager import WorkerUnavailable, worker_manager

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "local-model"
    messages: list[ChatMessage]
    max_tokens: int = Field(default=512, le=4096)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    stream: bool = False


@router.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [{"id": settings.model_id, "object": "model", "owned_by": "local"}],
    }


@router.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    _auth: None = Depends(verify_api_key),
    _rate: None = Depends(rate_limit_inference),
):
    if req.stream:
        raise HTTPException(status_code=400, detail="stream=true is not supported yet")

    try:
        result = await worker_manager.run_inference({
            "messages": [m.model_dump() for m in req.messages],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        })
    except WorkerUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"inference failed: {str(e)[:300]}")

    prompt_tokens = result.get("prompt_tokens", 0)
    completion_tokens = result.get("completion_tokens", 0)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": settings.model_id,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": result.get("text", "")},
            "finish_reason": "stop",
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
