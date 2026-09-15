# Serverless Colab GPU Inference Backend

Turns a Google Colab free GPU runtime into an on-demand inference worker,
controlled by a persistent backend (designed for Render). No local machine
or manually-opened Colab tab required — see `docs/architecture.md` for the
full design and `docs/research.md` for why it's built this way.

> Note: this repo also contains `README_COLAB.md`, `server_colab.py`,
> `connect_colab.sh`, etc. from an earlier, unrelated manual-notebook +
> cloudflared-tunnel experiment. They're untouched by this project and can
> be removed independently whenever you're done with them.

## Read first

1. `docs/research.md` — what's actually true about the official
   `google-colab-cli`, verified against a live install, not assumed.
2. `docs/architecture.md` — the resulting system design (and where it
   deliberately deviates from a naive design, and why).
3. `docs/colab-auth.md` — the one manual step (interactive Google login)
   that can't be automated, and how to carry its output into deployment.
4. `docs/deployment.md` — Render deployment walkthrough.

## Quickstart (local, no Colab required)

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements-dev.txt -r requirements-worker.txt
cp .env.example .env   # WORKER_MODE=mock by default

uvicorn controller.main:app --reload
```

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/status
curl -X POST http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hello"}]}'
```

The first inference request auto-starts a local mock worker subprocess
(`worker/mock_server.py`) and loads `MODEL_ID` on CPU; watch `/status` to
see it move through the state machine. It shuts itself down after
`WORKER_IDLE_TIMEOUT_SECONDS` of inactivity, same as the real thing.

## Running tests

```bash
python3 -m pytest tests/ -v
```

All 40 tests run against a fake driver / mocked subprocess — none of them
touch a real Colab GPU.

## Using a real Colab GPU

1. `uv tool install google-colab-cli && colab auth` (interactive, once —
   see `docs/colab-auth.md`).
2. `WORKER_MODE=colab` in `.env`, pick a real `MODEL_ID`.
3. `uvicorn controller.main:app` (same command) — now driven by a real T4.

`phase1_poc/run_poc.py` is a smaller, standalone script that proves just
the CLI lifecycle (provision → run → stop) in isolation, useful for
sanity-checking a fresh `colab auth` setup without spinning up the whole
controller.

## Deploying

See `docs/deployment.md`. Summary: `Dockerfile` + `render.yaml` deploy the
controller as a persistent Render web service; Colab auth is carried over
as a Render Secret File (never an env var, never committed — see
`docs/colab-auth.md`).
