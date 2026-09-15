"""The InferenceEngine abstraction (load/generate/health/shutdown), per the
brief. This module is plain, dependency-light-at-import-time Python so it
can run in two places identically:

  1. Imported directly by worker/mock_server.py for local dev (WORKER_MODE=mock).
  2. Uploaded via `colab upload` and imported inside the Colab kernel by
     worker/bootstrap.py (WORKER_MODE=colab, Design A -- see
     docs/architecture.md). There, `generate()` is called once per request
     via `colab exec`, not over HTTP.

Currently wired to Hcompany/Holo1.5-3B, a computer-use (GUI agent) vision-
language model fine-tuned from Qwen2.5-VL-3B-Instruct -- see
docs/research.md section 12 for why the loading code below (exact classes,
transformers version floor, image preprocessing) is what it is; none of it
was guessed, it's taken directly from the model's own published cookbook
notebook (github.com/hcompai/hai-cookbook), cross-checked against the raw
config.json.

Accepts OpenAI-vision-style messages: `content` is either a plain string,
or a list of `{"type": "text", "text": ...}` / `{"type": "image_url",
"image_url": {"url": "data:image/...;base64,..."}}` parts. Only base64
data URIs are supported for images, not arbitrary http(s) URLs -- fetching
a caller-supplied URL server-side would be an SSRF vector, and every
computer-use agent sends live screenshots as bytes, not hosted images.
"""
from __future__ import annotations

import base64
import time
from io import BytesIO
from typing import Any


class InferenceEngine:
    def __init__(self, model_id: str, revision: str | None = None,
                 dtype: str = "bfloat16", max_model_len: int = 4096):
        self.model_id = model_id
        self.revision = revision
        self.dtype = dtype
        self.max_model_len = max_model_len
        self._model = None
        self._processor = None
        self._device = "cpu"
        self._load_seconds: float | None = None

    def load(self) -> dict[str, Any]:
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        t0 = time.monotonic()
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        torch_dtype = getattr(torch, self.dtype, torch.bfloat16)

        self._processor = AutoProcessor.from_pretrained(self.model_id, revision=self.revision)
        self._model = AutoModelForImageTextToText.from_pretrained(
            self.model_id,
            revision=self.revision,
            dtype=torch_dtype,
            device_map=self._device,
        )
        self._model.eval()
        self._load_seconds = round(time.monotonic() - t0, 1)

        info = {"model": self.model_id, "device": self._device, "load_seconds": self._load_seconds}
        if self._device == "cuda":
            info["gpu_name"] = torch.cuda.get_device_name(0)
        return info

    def generate(self, messages: list[dict[str, Any]], max_new_tokens: int = 512,
                 temperature: float = 0.7) -> dict[str, Any]:
        if self._model is None:
            raise RuntimeError("InferenceEngine.generate() called before load()")
        import torch

        t0 = time.monotonic()
        hf_messages, images, image_info = self._build_hf_messages(messages)
        text_prompt = self._processor.apply_chat_template(
            hf_messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self._processor(
            text=[text_prompt],
            images=images or None,
            padding=True,
            return_tensors="pt",
        ).to(self._device)

        with torch.no_grad():
            output_ids = self._model.generate(
                **inputs,
                max_new_tokens=max_new_tokens,
                temperature=max(temperature, 1e-4),
                do_sample=temperature > 0,
            )
        new_tokens = output_ids[0][inputs["input_ids"].shape[1]:]
        text = self._processor.batch_decode(
            [new_tokens], skip_special_tokens=True, clean_up_tokenization_spaces=False
        )[0]

        result = {
            "text": text,
            "prompt_tokens": int(inputs["input_ids"].shape[1]),
            "completion_tokens": int(new_tokens.shape[0]),
            "latency_seconds": round(time.monotonic() - t0, 2),
        }
        if image_info:
            # Coordinates the model outputs are relative to the RESIZED
            # image, not whatever the caller uploaded -- the agent needs
            # these dimensions to scale a predicted (x, y) back to real
            # screen/screenshot coordinates.
            result["images"] = image_info
        return result

    def _build_hf_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list, list[dict[str, Any]]]:
        from PIL import Image
        from transformers.models.qwen2_vl.image_processing_qwen2_vl import smart_resize

        ip = self._processor.image_processor
        hf_messages = []
        images: list[Image.Image] = []
        image_info: list[dict[str, Any]] = []

        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                hf_messages.append({"role": msg["role"], "content": [{"type": "text", "text": content}]})
                continue

            hf_content = []
            for part in content:
                part_type = part.get("type")
                if part_type == "text":
                    hf_content.append({"type": "text", "text": part.get("text", "")})
                elif part_type == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if not url.startswith("data:"):
                        raise ValueError(
                            "image_url.url must be a data: URI (base64) -- "
                            "fetching arbitrary URLs server-side is not supported"
                        )
                    _, b64data = url.split(",", 1)
                    raw = base64.b64decode(b64data)
                    image = Image.open(BytesIO(raw)).convert("RGB")

                    resized_h, resized_w = smart_resize(
                        image.height, image.width,
                        factor=ip.patch_size * ip.merge_size,
                        min_pixels=ip.min_pixels,
                        max_pixels=ip.max_pixels,
                    )
                    resized = image.resize((resized_w, resized_h), Image.Resampling.LANCZOS)
                    images.append(resized)
                    image_info.append({
                        "original_width": image.width, "original_height": image.height,
                        "resized_width": resized_w, "resized_height": resized_h,
                    })
                    hf_content.append({"type": "image", "image": resized})
                else:
                    raise ValueError(f"unsupported content part type: {part_type!r}")
            hf_messages.append({"role": msg["role"], "content": hf_content})

        return hf_messages, images, image_info

    def health(self) -> dict[str, Any]:
        return {
            "loaded": self._model is not None,
            "device": self._device,
            "model": self.model_id,
        }

    def shutdown(self) -> None:
        self._model = None
        self._processor = None
