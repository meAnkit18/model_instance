"""The InferenceEngine abstraction (load/generate/health/shutdown), per the
brief. This module is plain, dependency-light-at-import-time Python so it
can run in two places identically:

  1. Imported directly by worker/mock_server.py for local dev (WORKER_MODE=mock).
  2. Uploaded via `colab upload` and imported inside the Colab kernel by
     worker/bootstrap.py (WORKER_MODE=colab, Design A -- see
     docs/architecture.md). There, `generate()` is called once per request
     via `colab exec`, not over HTTP.

Swapping the backend (transformers -> llama-cpp-python, say) means editing
only this file -- see docs/research.md section 7 for why transformers was
chosen first.
"""
from __future__ import annotations

import time
from typing import Any


class InferenceEngine:
    def __init__(self, model_id: str, revision: str | None = None,
                 dtype: str = "bfloat16", max_model_len: int = 4096):
        self.model_id = model_id
        self.revision = revision
        self.dtype = dtype
        self.max_model_len = max_model_len
        self._model = None
        self._tokenizer = None
        self._device = "cpu"
        self._load_seconds: float | None = None

    def load(self) -> dict[str, Any]:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        t0 = time.monotonic()
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        torch_dtype = getattr(torch, self.dtype, torch.float32)

        self._tokenizer = AutoTokenizer.from_pretrained(
            self.model_id, revision=self.revision
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_id,
            revision=self.revision,
            torch_dtype=torch_dtype,
            device_map=self._device,
        )
        self._model.eval()
        self._load_seconds = round(time.monotonic() - t0, 1)

        info = {"model": self.model_id, "device": self._device, "load_seconds": self._load_seconds}
        if self._device == "cuda":
            info["gpu_name"] = torch.cuda.get_device_name(0)
        return info

    def generate(self, messages: list[dict[str, str]], max_new_tokens: int = 512,
                 temperature: float = 0.7) -> dict[str, Any]:
        if self._model is None:
            raise RuntimeError("InferenceEngine.generate() called before load()")
        import torch

        t0 = time.monotonic()
        prompt = self._render_prompt(messages)
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._device)
        with torch.no_grad():
            output_ids = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                temperature=max(temperature, 1e-4),
                do_sample=temperature > 0,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        new_tokens = output_ids[0][inputs["input_ids"].shape[1]:]
        text = self._tokenizer.decode(new_tokens, skip_special_tokens=True)

        return {
            "text": text,
            "prompt_tokens": int(inputs["input_ids"].shape[1]),
            "completion_tokens": int(new_tokens.shape[0]),
            "latency_seconds": round(time.monotonic() - t0, 2),
        }

    def _render_prompt(self, messages: list[dict[str, str]]) -> str:
        """Model-agnostic: not every configured MODEL_ID ships a chat
        template (e.g. small/base models), so fall back to a plain
        role-tagged transcript rather than crashing the request."""
        if getattr(self._tokenizer, "chat_template", None):
            return self._tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        lines = [f"{m['role']}: {m['content']}" for m in messages]
        lines.append("assistant:")
        return "\n".join(lines)

    def health(self) -> dict[str, Any]:
        return {
            "loaded": self._model is not None,
            "device": self._device,
            "model": self.model_id,
        }

    def shutdown(self) -> None:
        self._model = None
        self._tokenizer = None
