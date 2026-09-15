"""Colab worker bootstrap (Design A -- docs/architecture.md).

This file is a TEMPLATE, not run directly. `controller/services/
colab_driver.py` reads it and substitutes the model-config marker (see the
`_CONFIG = json.loads(...)` line below) with a properly escaped JSON
string literal -- never raw-interpolated -- then executes the result once
via `ColabManager.exec_script()` in the session's persistent Jupyter
kernel.

Responsibilities (per the brief's Component 2 list, sections 1-5; sections
6-10 -- register/heartbeat/receive-requests/shutdown -- are handled by the
controller pulling via further `colab exec` calls, not pushed by the
worker, since no inbound path to the VM exists -- see docs/research.md
section 5):
    1. identify runtime            -> WORKER_READY_JSON includes gpu_name
    2. install deps if necessary   -> done by the controller via
                                       `colab install -r requirements-worker.txt`
                                       before this script runs
    3. load the configured model   -> ENGINE.load()
    4. start an inference "server" -> _handle_request() defined in kernel
                                       globals, called per-request by
                                       small exec snippets built by
                                       controller/services/inference_driver.py
"""
import json
import sys
import time

sys.path.insert(0, "/content")
from inference import InferenceEngine  # noqa: E402  (uploaded by the controller first)

_CONFIG = json.loads("__MODEL_CONFIG_JSON__")

ENGINE = InferenceEngine(
    model_id=_CONFIG["model_id"],
    revision=_CONFIG.get("revision"),
    dtype=_CONFIG.get("dtype", "bfloat16"),
    max_model_len=_CONFIG.get("max_model_len", 4096),
    max_image_pixels=_CONFIG.get("max_image_pixels", 1_003_520),
)

_t0 = time.monotonic()
_load_info = ENGINE.load()
_load_info["load_wall_seconds"] = round(time.monotonic() - _t0, 1)


def _handle_request(request_json: str) -> str:
    """Invoked by the per-request snippets `inference_driver.py` builds.
    Kept as a plain function in kernel globals rather than a running HTTP
    server -- Design A drives it via `colab exec`, one call per request."""
    req = json.loads(request_json)
    result = ENGINE.generate(
        req["messages"],
        max_new_tokens=req.get("max_tokens", 512),
        temperature=req.get("temperature", 0.7),
    )
    return json.dumps(result)


print("WORKER_READY_JSON:" + json.dumps(_load_info))
