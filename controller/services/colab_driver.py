"""Design A worker driver: turns the abstract "start/infer/health/stop"
lifecycle into `colab` CLI calls against a persistent Jupyter kernel.
No HTTP server runs on the Colab VM -- see docs/architecture.md."""
from __future__ import annotations

import json
from pathlib import Path

from controller.config import settings
from controller.services.colab_manager import ColabError, GpuUnavailableError, colab_manager

_WORKER_DIR = Path(__file__).resolve().parent.parent.parent / "worker"
_BOOTSTRAP_TEMPLATE = (_WORKER_DIR / "bootstrap.py").read_text()
_INFERENCE_MODULE = _WORKER_DIR / "inference.py"
_REQUIREMENTS = Path(__file__).resolve().parent.parent.parent / "requirements-worker.txt"


def _extract_marker(stdout: str, marker: str) -> dict:
    for line in stdout.splitlines():
        if line.startswith(marker):
            return json.loads(line[len(marker):])
    # Surface the script's actual output (traceback, etc.) rather than
    # just "marker not found" -- this is what makes MODEL_LOAD_FAILED
    # diagnosable instead of a black box.
    raise ColabError(
        f"expected output marker '{marker}' not found in exec output",
        0, stdout[-4000:], ""
    )


class ColabWorkerDriver:
    """Stateless wrapper -- all persistent state lives in controller.state."""

    def __init__(self, session_name: str | None = None):
        self.session_name = session_name or settings.colab_session_name

    def start(self) -> dict:
        """Provision the runtime, install deps, load the model. Raises
        GpuUnavailableError / ColabError on failure -- callers translate
        those into the ERROR state per docs/architecture.md."""
        colab_manager.create_runtime(self.session_name, settings.colab_gpu)
        colab_manager.upload_file(
            self.session_name, str(_INFERENCE_MODULE), "/content/inference.py"
        )
        colab_manager.install_requirements(
            self.session_name, str(_REQUIREMENTS), timeout=settings.startup_timeout_seconds
        )

        # The template has `_CONFIG = json.loads("__MODEL_CONFIG_JSON__")` --
        # the marker sits INSIDE Python string quotes, so what replaces it
        # must itself be a valid escaped Python string literal body, not
        # raw JSON. json.dumps(json.dumps(...)) double-encodes: the inner
        # dumps produces the JSON text, the outer dumps produces a
        # properly quote-escaped Python string literal containing that
        # text (minus its own surrounding quotes, stripped below). Same
        # pattern used for per-request payloads in `infer()`.
        model_config_json = json.dumps({
            "model_id": settings.model_id,
            "revision": settings.model_revision,
            "dtype": settings.model_dtype,
            "max_model_len": settings.max_model_len,
        })
        escaped_literal = json.dumps(model_config_json)[1:-1]  # strip outer quotes
        script = _BOOTSTRAP_TEMPLATE.replace("__MODEL_CONFIG_JSON__", escaped_literal)
        stdout = colab_manager.exec_script(
            self.session_name, script, timeout=settings.startup_timeout_seconds
        )
        return _extract_marker(stdout, "WORKER_READY_JSON:")

    def infer(self, request: dict) -> dict:
        request_literal = json.dumps(json.dumps(request))
        snippet = (
            "import json\n"
            f"print('INFER_RESULT_JSON:' + _handle_request({request_literal}))\n"
        )
        stdout = colab_manager.exec_script(
            self.session_name, snippet, timeout=settings.inference_timeout_seconds
        )
        return _extract_marker(stdout, "INFER_RESULT_JSON:")

    def health(self) -> dict | None:
        """Pull-based heartbeat equivalent (docs/architecture.md): a
        successful `colab status` is treated as the liveness signal.
        Cheap -- does not touch the kernel."""
        info = colab_manager.status(self.session_name)
        if info is None:
            return None
        return {"status": info.status, "hardware": info.hardware}

    def stop(self) -> None:
        colab_manager.stop_runtime(self.session_name)
