"""Unit tests for the OpenAI-vision-style message building in
worker/inference.py -- the part most likely to silently mishandle a
malformed request, since it only ever runs for real inside a Colab
kernel where a bug is expensive to discover (docs/research.md section 11
is a whole catalog of exactly that risk)."""
import base64
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from worker.inference import InferenceEngine


def _fake_engine() -> InferenceEngine:
    engine = InferenceEngine(model_id="test/fake-vlm")
    # _build_hf_messages only touches processor.image_processor's sizing
    # fields -- no real model/processor needed for this unit.
    engine._processor = SimpleNamespace(
        image_processor=SimpleNamespace(patch_size=14, merge_size=2, min_pixels=56 * 56, max_pixels=1000 * 1000)
    )
    return engine


def _data_uri(width: int, height: int) -> str:
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def test_text_only_message_produces_text_content_part():
    engine = _fake_engine()
    hf_messages, images, image_info = engine._build_hf_messages(
        [{"role": "user", "content": "hello"}]
    )
    assert hf_messages == [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    assert images == []
    assert image_info == []


def test_image_content_part_is_decoded_and_resized():
    engine = _fake_engine()
    uri = _data_uri(1920, 1080)
    hf_messages, images, image_info = engine._build_hf_messages([
        {"role": "user", "content": [
            {"type": "text", "text": "click the button"},
            {"type": "image_url", "image_url": {"url": uri}},
        ]},
    ])

    assert len(images) == 1
    assert len(image_info) == 1
    assert image_info[0]["original_width"] == 1920
    assert image_info[0]["original_height"] == 1080
    # smart_resize keeps aspect ratio and snaps to the patch*merge grid
    assert image_info[0]["resized_width"] % (14 * 2) == 0
    assert image_info[0]["resized_height"] % (14 * 2) == 0

    content_types = [p["type"] for p in hf_messages[0]["content"]]
    assert content_types == ["text", "image"]


def test_non_data_uri_image_url_is_rejected():
    engine = _fake_engine()
    with pytest.raises(ValueError, match="data:"):
        engine._build_hf_messages([
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "https://example.com/shot.png"}},
            ]},
        ])


def test_unsupported_content_part_type_is_rejected():
    engine = _fake_engine()
    with pytest.raises(ValueError, match="unsupported content part type"):
        engine._build_hf_messages([
            {"role": "user", "content": [{"type": "video_url", "video_url": {"url": "x"}}]},
        ])


def test_generate_before_load_raises():
    engine = InferenceEngine(model_id="test/fake-vlm")
    with pytest.raises(RuntimeError, match="before load"):
        engine.generate([{"role": "user", "content": "hi"}])
