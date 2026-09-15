# Architecture: Render Controller + Colab-CLI-driven GPU worker

Read `docs/research.md` first — this document implements the corrected design from §5 and §8 of that research, not the brief's original Component 2 networking assumption.

## System overview

```text
Client / AI Agent
       |
       | POST /v1/chat/completions
       v
+-------------------------------------------------------------+
|                      Render Controller                       |
|  (persistent Linux service — must NOT scale to zero, since   |
|   the Colab keep-alive daemon must keep running)              |
|                                                                |
|  api/            FastAPI: /health /status /v1/*               |
|  services/                                                    |
|    colab_manager.py   -- all `colab` CLI subprocess calls     |
|    session_state.py   -- persists ~/.config/colab-cli/* +     |
|                           lifecycle metadata across restarts  |
|    worker_manager.py  -- state machine, startup lock          |
|    lifecycle_manager.py -- background loop: idle timeout,     |
|                           health polling, retries             |
|    inference_driver.py -- turns a chat request into a         |
|                           `colab exec` call + parses result   |
+---------------------------+-----------------------------------+
                            |
                            | subprocess: `colab exec -s worker -f snippet.py`
                            | (Google's own authenticated tunnel;
                            |  this is the ONLY channel — see research.md §5)
                            v
+-------------------------------------------------------------+
|                   Google Colab GPU runtime                   |
|         (one persistent Jupyter kernel, kept alive by        |
|          the CLI's local keep-alive daemon on Render)         |
|                                                                |
|   worker/bootstrap.py  -- executed once via `colab exec`,     |
|     loads the model and defines generate(prompt) in the       |
|     kernel's global namespace, then returns                    |
|   worker/infer_snippet.py -- executed once per request,        |
|     calls generate(...), prints JSON to stdout                |
+-------------------------------------------------------------+
```

**Why there is no worker-side HTTP server, registration endpoint, or heartbeat-push protocol:** research.md §5 established there is no inbound path to a Colab VM and no supported way to expose a port from it. The CLI subprocess on Render *is* the live connection. This collapses "worker registers with Render" into "the `colab exec` that loads the model returned exit code 0," and "heartbeat" into "Render's lifecycle loop successfully ran `colab status -s worker` in the last `HEARTBEAT_TIMEOUT_SECONDS`." Everything else the brief asked for (state machine, idle shutdown, startup lock, retries, status/metrics API, OpenAI-compatible client API, mock mode, security) is preserved as specified.

`WORKER_MODE=mock` is unaffected by any of this — the mock worker is a real local HTTP process (Component 2 as originally specified, minus the Colab-specific transport), so `worker/` still contains a small FastAPI app usable standalone for local dev/tests, plus the `bootstrap.py`/`infer_snippet.py` pair used under `WORKER_MODE=colab`.

## State machine (unchanged from the brief, semantics adjusted)

```text
OFFLINE --request--> STARTING --colab new ok--> BOOTING --exec bootstrap.py ok--> LOADING_MODEL --generate() defined ok--> READY
READY --no requests for WORKER_IDLE_TIMEOUT_SECONDS--> STOPPING --colab stop--> OFFLINE
READY --HEARTBEAT_TIMEOUT_SECONDS since last successful colab status/exec--> ERROR --retry w/ backoff--> STARTING
STARTING --GPU_UNAVAILABLE or colab new fails--> ERROR (bounded retries, then surfaced to caller as 503)
```

`STARTING` acquires an asyncio/process-level lock (`worker_manager.py`) before calling `ColabManager.create_and_boot()`; every concurrent request checks-then-waits on the same in-flight future rather than starting a second session — this directly implements the brief's "20 requests → one STARTING operation" requirement.

## Component boundaries (mapping to `project/` structure)

- `controller/services/colab_manager.py` — the *only* place that shells out to `colab`. All calls use `subprocess.run([...])` argument arrays (never a shell string), with `HOME` overridden to `settings.colab_home_dir` for every subprocess call — the CLI resolves its token/session cache relative to `$HOME` and has no other override mechanism (found by live testing a first, broken deploy attempt; see docs/research.md section 11). Methods: `create_runtime(gpu)`, `exec(session, script_path, timeout)`, `status(session)`, `stop(session)`, `list_sessions()`. Every one of these maps to a real, researched command (`colab new`, `colab exec`, `colab status`, `colab stop`, `colab sessions`) — nothing invented.
- `controller/services/inference_driver.py` — turns an OpenAI-style chat request into the small Python snippet passed to `colab exec`, and parses the stdout JSON back into a response. This is the `InferenceProxy` from the brief, just implemented over `exec` instead of `httpx` to a worker URL.
- `controller/services/worker_manager.py` / `lifecycle_manager.py` — state machine + background loop, as specified.
- `controller/state.py` — persists lifecycle metadata (current state, session name, last request/heartbeat timestamps) **and** ensures `~/.config/colab-cli/` lives on a durable path, so a Render restart can rediscover a still-running Colab session per research.md §4 rather than assuming it's gone.
- `worker/bootstrap.py`, `worker/inference.py` — the code that gets `colab exec`'d into the kernel. `inference.py` is the `InferenceEngine` abstraction (`load()`, `generate()`, `health()`), started with a `transformers`-based implementation per research.md §7.
- `worker/mock_server.py` — standalone FastAPI app implementing the same registration/heartbeat/inference contract as originally specified, used only when `WORKER_MODE=mock`.

## Request flow (Case 2 — worker OFFLINE), concretely

```text
1. POST /v1/chat/completions arrives, WorkerManager state == OFFLINE
2. WorkerManager acquires startup lock (no-op if already held; caller awaits existing future)
3. ColabManager.create_runtime(gpu=T4)      -> `colab new -s worker --gpu T4 --keep`
      - on GPU_UNAVAILABLE: bounded retry/backoff (STARTUP_RETRY_*), else -> ERROR, 503 to all waiters
4. ColabManager.exec(worker, bootstrap.py)  -> `colab exec -s worker -f worker/bootstrap.py --timeout <STARTUP_TIMEOUT_SECONDS>`
      - installs deps if missing, downloads/loads model, defines generate(), returns {"status":"ready","gpu":...}
      - on failure -> ERROR, diagnostics captured from exec output, retry per config
5. state -> READY; lifecycle_manager starts polling `colab status` every HEARTBEAT_INTERVAL_SECONDS
6. inference_driver.run(original request)    -> `colab exec -s worker -f infer_snippet.py` (prompt passed via a temp file, not argv, to avoid shell-arg limits/injection)
7. response parsed from stdout JSON, returned to client
8. last_request timestamp updated; subsequent requests skip 2-5 entirely while READY
```

Concurrent requests during STARTING are queued in-process on the shared future from step 2 — no second `colab new` is issued (satisfies the "one STARTING operation" requirement without needing an external lock service, since Render is a single persistent process).

## Fallback design (documented, not built yet)

If Phase 4 measurement shows `colab exec` per-request latency/reliability is unacceptable (plausible given the CLI is ~3 months old — research.md §4), **Design B** is the escape hatch: `bootstrap.py` launches the inference server as a detached background process inside the VM (`subprocess.Popen`, freeing the kernel), which then performs outbound HTTPS `POST /internal/workers/register` and `POST /internal/workers/heartbeat` to Render exactly as the original brief specified, and receives work via outbound long-polling (`GET /internal/workers/next-request`) since inbound to the VM still isn't possible. This is a larger change (needs the worker registration secret, internal endpoint auth, a request-matching layer on Render) and is deliberately not built until Design A is proven insufficient — building it speculatively would be the "overengineer this" mistake the brief explicitly warns against.

## Phase 1: done, live

`phase1_poc/run_poc.py` provisioned a real T4, ran `phase1_poc/gpu_check.py` on it, printed GPU identity, and tore the runtime down cleanly — in 24.6s total (15.1s provision + 8.3s exec) on a live run 2026-09-15. This account already had a cached `colab auth` token from earlier work in this repo, so no manual auth step was needed here. See research.md §9 for the raw output.

## Remaining open items before going further into Phase 2+

1. **Policy risk acceptance** (research.md §6) — proceeding on the recommendation to use the official CLI as intended, but Colab's abuse-detection has a documented false-positive history; worth not pointing sustained automated traffic at an account you can't afford to have flagged, at least initially.
2. **Design A vs. B** — building Design A (exec-driven, no worker HTTP server) as the default per the analysis above. This is a real deviation from the brief's Component 2 write-up (no worker registration/heartbeat-push endpoints), even though it satisfies every numbered success criterion and is simpler to build and secure.
3. **Auth durability for a real Render deploy** — the token that just worked lives at `~/.config/colab-cli/` on this machine. Deploying to Render means exporting that directory and mounting it as a Render secret file/persistent disk; it is tied to one Google account and will need manual re-auth if Google ever revokes it (research.md §2).
