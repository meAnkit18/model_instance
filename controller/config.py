"""All configuration lives here as environment variables with sensible
defaults -- see .env.example. Nothing here reads a value hard-coded
elsewhere in the codebase (per the brief's "do not hard-code" rules)."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Worker transport ---
    # "mock": a local HTTP worker process, for development without touching Colab.
    # "colab": drive a real Colab GPU runtime via the ColabManager (Design A --
    #          see docs/architecture.md -- no worker-side HTTP server involved).
    worker_mode: str = "mock"

    # --- Model (model-agnostic; swap without touching code) ---
    # Hcompany/Holo1.5-3B: a computer-use (GUI agent) vision-language model
    # -- see docs/research.md section 12. Accepts image_url content parts
    # (base64 data URIs) alongside text in /v1/chat/completions.
    model_id: str = "Hcompany/Holo1.5-3B"
    model_revision: str | None = None
    model_dtype: str = "bfloat16"
    max_model_len: int = 4096

    # --- Colab ---
    colab_gpu: str = "T4"
    colab_session_name: str = "inference-worker"
    # The `colab` CLI resolves its token/session cache relative to $HOME
    # (~/.config/colab-cli/, ~/.colab-cli-oauth-config.json) -- it does NOT
    # read a COLAB_CONFIG_DIR-style env var (verified live; see
    # docs/research.md section 11). ColabManager overrides HOME for the
    # subprocess to this directory, so the Render Secret File (copied here
    # by docker-entrypoint.sh) is actually where the CLI looks.
    colab_home_dir: Path = Path.home()
    colab_bin: str = "colab"

    # --- Lifecycle timing ---
    worker_idle_timeout_seconds: int = 600
    heartbeat_interval_seconds: int = 30
    heartbeat_timeout_seconds: int = 90
    startup_timeout_seconds: int = 900
    inference_timeout_seconds: int = 300
    startup_max_retries: int = 3
    startup_retry_backoff_seconds: int = 20

    # --- Concurrency / request handling ---
    max_concurrent_requests: int = 4
    # A single base64-encoded screenshot (e.g. a 1920x1080 PNG) can easily
    # exceed 1-2MB on its own -- the old 1MB default (fine for a text-only
    # model) would reject a normal computer-use request outright.
    max_request_body_bytes: int = 10_000_000

    # --- Security ---
    api_key: str | None = None  # required bearer token for public API, if set
    worker_registration_secret: str | None = None  # used only by mock worker path

    # --- Mock worker (WORKER_MODE=mock) ---
    mock_worker_host: str = "127.0.0.1"
    mock_worker_port: int = 8901

    # --- State persistence ---
    state_file: Path = Path("./data/controller_state.json")


settings = Settings()
