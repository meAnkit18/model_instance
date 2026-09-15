# Deploying the controller to Render

Read `docs/architecture.md` and `docs/colab-auth.md` first.

## Prerequisites

1. A Google account with Colab access, and `google-colab-cli` authenticated
   locally (`docs/colab-auth.md`) — you need the resulting `token.json`.
2. A Render account, this repo pushed to a Git provider Render can deploy from.
3. Decide on `MODEL_ID` (start with the small model in `.env.example`;
   swap to your real 2-3B model once the pipeline is proven, per the brief's
   phased approach).

## Steps

### 1. Create the Render service

From the Render dashboard: **New → Blueprint**, point it at this repo. It
will read `render.yaml` and propose a `colab-inference-controller` web
service backed by the `Dockerfile`, with a 1GB persistent disk mounted at
`/data`. Confirm the plan is one that does **not** scale to zero/idle —
the Colab keep-alive daemon (docs/research.md section 4) needs a
continuously-running process, not a cold-starting function.

### 2. Configure environment variables

Everything with a default in `render.yaml` is pre-filled. You must set,
in the dashboard (never in the repo):

- `API_KEY` — generate a real random value (e.g. `openssl rand -hex 32`).
  Clients call `/v1/chat/completions` with `Authorization: Bearer <API_KEY>`.
- `WORKER_REGISTRATION_SECRET` — only meaningful if you ever run
  `WORKER_MODE=mock` against this deployment; safe to set anyway.

### 3. Configure Colab authentication

Dashboard → your service → **Environment → Secret Files**. Upload your
local `~/.config/colab-cli/token.json` and `~/.config/colab-cli/settings.json`
to `/data/colab-cli/token.json` and `/data/colab-cli/settings.json`
(matching `COLAB_CONFIG_DIR=/data/colab-cli` from `render.yaml`). See
`docs/colab-auth.md` for exactly why this step can't be automated.

### 4. Deploy

Trigger the deploy (Blueprint apply, or push to the connected branch).
Watch the build logs — the `Dockerfile` installs `google-colab-cli` and
the Python dependencies; nothing here should require network access to
Colab itself at build time.

### 5. Verify `/health`

```bash
curl https://<your-service>.onrender.com/health
# {"status": "healthy"}
```

This only proves the controller process is up — it says nothing about
Colab connectivity yet.

### 6. Trigger `/status`

```bash
curl https://<your-service>.onrender.com/status
```

Expect `worker.state: "OFFLINE"` on a fresh deploy. If this instead shows
a `colab_cli_invoke`/auth-related `error_message`, step 3 didn't land
correctly — re-check the secret file paths against `COLAB_CONFIG_DIR`.

### 7. Trigger the first inference

```bash
curl -X POST https://<your-service>.onrender.com/v1/chat/completions \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hello"}]}'
```

First call will be slow (cold start: GPU provisioning + dependency install
+ model download/load — see docs/research.md section 10 for real measured
numbers on a small model). Poll `/status` in another terminal to watch
`worker.state` move `OFFLINE → STARTING → BOOTING → LOADING_MODEL → READY`.
Subsequent calls within `WORKER_IDLE_TIMEOUT_SECONDS` reuse the same
worker and should be fast (measured ~2s round-trip on a warm T4 session
for a short generation — research.md section 10).

## What to expect operationally

- After `WORKER_IDLE_TIMEOUT_SECONDS` (default 600s) of no requests, the
  lifecycle manager stops the Colab runtime automatically — `worker.state`
  returns to `OFFLINE`, and the next request repeats step 7's cold start.
- A Render restart does not lose the Colab session outright (session
  metadata persists on the `/data` disk) — but re-verify `/status` after
  any restart, since research.md section 4's reliability caveats about
  this ~3-month-old CLI still apply.
- Watch Render's logs for the structured JSON events listed in
  `docs/architecture.md` (`startup_requested`, `model_ready`,
  `idle_timeout`, `worker_unhealthy`, etc.) — every lifecycle transition
  logs one.
