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
    model_id: str = "Qwen/Qwen2.5-1.5B-Instruct"
    model_revision: str | None = None
    model_dtype: str = "bfloat16"
    max_model_len: int = 4096

    # --- Colab ---
    colab_gpu: str = "T4"
    colab_session_name: str = "inference-worker"
    colab_config_dir: Path = Path.home() / ".config" / "colab-cli"
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
    max_request_body_bytes: int = 1_000_000

    # --- Security ---
    api_key: str | None = None  # required bearer token for public API, if set
    worker_registration_secret: str | None = None  # used only by mock worker path

    # --- Mock worker (WORKER_MODE=mock) ---
    mock_worker_host: str = "127.0.0.1"
    mock_worker_port: int = 8901

    # --- State persistence ---
    state_file: Path = Path("./data/controller_state.json")


settings = Settings()
