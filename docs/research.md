# Research: Google Colab CLI feasibility for a Render-driven, on-demand GPU inference backend

Date: 2026-09-15
Status: Phase 0 complete. This document must be read before touching `controller/` or `worker/` code.

## TL;DR

- An **official** `google-colab-cli` exists (Apache-2.0, `googlecolab/google-colab-cli` on GitHub, announced on the Google Developers Blog, June 5 2026). It is exactly the kind of tool the brief assumed — this is good news, the premise was not wrong.
- Headless, non-interactive authentication **is supported** via a device-code-style OAuth2 flow that caches a refresh token to disk (`~/.config/colab-cli/token.json`). This can be done once, by a human, anywhere with a browser, and the resulting token file can be shipped to Render as a secret and reused non-interactively indefinitely (until Google revokes/expires it).
- The CLI can provision GPU sessions (`colab new --gpu T4 --keep`), run code in them (`colab exec`), and tear them down (`colab stop`) — all scriptable, no shell-injection-prone tricks needed.
- **The originally-assumed networking model is wrong and must change.** There is no way for Render to open an inbound HTTP connection to a Colab VM, and there is no way for a worker process on the VM to expose a port Render can reach. The *only* channel between Render and the VM is the CLI's own authenticated RPC tunnel (`colab exec`/`colab run`), which Render already holds by virtue of owning the OAuth token. This actually **simplifies** the architecture (see `docs/architecture.md`) — we don't need a worker-side HTTP server, registration endpoint, or heartbeat-push protocol at all; Render can drive inference by directly executing code in the persistent Colab kernel.
- **Unresolved policy tension**: Colab's free-tier FAQ explicitly forbids "remote control such as SSH shells, remote desktops" and "bypassing the notebook UI" on free runtimes — which is a literal description of what the official CLI's `exec`/`ssh`/`console`/`run` commands do. Google ships and promotes this CLI for exactly this use case (including explicit "AI agent" integration), so the most reasonable reading is that the FAQ clause targets third-party hacks that predate the official tool (Selenium bots, SSH-tunnel shims) rather than Google's own sanctioned CLI — but no source found reconciles this explicitly. This is a real ambiguity, not a solved problem; flagging it for your own risk tolerance rather than deciding it for you (see "Policy risk" below).
- The CLI is young (shipped ~3 months ago) and has open reliability bugs for unattended sessions. Design the controller assuming the worker session can die/hang at any time — which the spec already asks for (heartbeat timeout → OFFLINE, restart) — so this doesn't change the required design, just raises the importance of actually building the failure-handling paths rather than treating them as edge cases.

---

## 1. Does an official Colab CLI exist, and does it do what the brief assumes?

Yes. `google-colab-cli` (GitHub: `googlecolab/google-colab-cli`, PyPI: `google-colab-cli`) was announced by Google on the Developers Blog on 2026-06-05 ([Introducing the Google Colab CLI](https://developers.googleblog.com/introducing-the-google-colab-cli/)), covered by [MarkTechPost](https://www.marktechpost.com/2026/06/06/googles-new-colab-cli-lets-developers-and-ai-agents-run-python-on-remote-colab-gpus-and-tpus-from-the-terminal/), [InfoQ](https://www.infoq.com/news/2026/06/google-colab-cli/), and [Help Net Security](https://www.helpnetsecurity.com/2026/06/08/google-colab-command-line-interface-cli/) (the latter covering it from a security-risk angle, but without resolving the ToS question — see below).

- License: Apache 2.0. Install: `uv tool install google-colab-cli` or `pip install google-colab-cli`.
- Platforms: **Linux and macOS only. No Windows support.** (Fine for Render, which runs Linux containers.)
- It ships `COLAB_SKILL.md`/`AGENTS.md` specifically so terminal-driven coding agents (Claude Code, Codex, Antigravity) can drive it — Google's own framing is "developers and AI agents," which is directly relevant to an automated-orchestrator use case like this project.

### Relevant commands (from the official README and `docs/*.md` in the repo)

| Command | Purpose |
|---|---|
| `colab auth [-s NAME]` | Authenticate the CLI/VM |
| `colab new [-s NAME] [--gpu GPU] [--tpu TPU] [--high-mem]` | Allocate a CPU/GPU/TPU session |
| `colab sessions` | List active sessions |
| `colab status [-s NAME]` | Hardware, machine shape, status |
| `colab exec [-s NAME] [-f FILE]` | Execute Python (stdin, `.py`, or `.ipynb`) in a session's kernel |
| `colab run [--gpu G] [--keep] SCRIPT [ARGS]` | Ephemeral job: provision → execute → release (unless `--keep`) |
| `colab stop [-s NAME]` | Terminate the session VM and its keep-alive daemon |
| `colab ssh` / `colab console` / `colab repl` | Interactive access (TTY-bound; not useful for a server-driven orchestrator) |
| `colab log [-s NAME]` | Export session history as `.ipynb`/`.md`/`.jsonl` |
| `colab upload` / `colab download` / `colab ls` / `colab rm` | File operations against the VM |
| `colab install [-r FILE \| PKG...]` | Installs packages via `uv`/`pip` on the VM |

GPU options: **T4, L4, G4, H100, A100**. TPU: v5e1, v6e1. `--high-mem` requires a Colab Pro/Pro+ entitlement on supported accelerators; T4/L4 do not need a paid plan but free-tier GPU access is explicitly described by Google as "heavily restricted" and availability "varies over time."

**Conclusion:** `colab new`, `colab exec`, `colab run`, `colab stop`, `--keep`, and GPU selection all exist and behave close to what the brief assumed. This is the one part of the original plan that research *confirmed* rather than corrected.

Sources: [GitHub README](https://github.com/googlecolab/google-colab-cli/blob/main/README.md), [Google Developers Blog](https://developers.googleblog.com/introducing-the-google-colab-cli/), [PyPI](https://pypi.org/project/google-colab-cli/).

---

## 2. Authentication and headless/non-interactive auth

- Default auth strategy is Application Default Credentials (ADC); alternative is OAuth2, selected with the global `--auth {oauth2,adc}` flag.
- The OAuth2 flow used is **not** a localhost-callback flow and **not** the old out-of-band (OOB) `urn:ietf:wg:oauth:2.0:oob` flow (Google disabled OOB in 2022 — a real, well-documented restriction). Instead it's a "remote copy-paste" flow: the CLI prints a URL, a human opens it in *any* browser (doesn't need to be on the same machine), signs in, and pastes back a code. This is the same mechanism `gcloud auth application-default login` on a headless/remote host uses, and it works identically on containers.
- The resulting refresh token is cached at `~/.config/colab-cli/token.json`. Session metadata (per-session bearer token, backend URL, hardware) is cached separately at `~/.config/colab-cli/sessions.json`, both paths overridable via `--config`.

**Implication for this project:** a human (you) runs `colab auth` once, interactively, on a laptop or in this dev sandbox. The resulting `~/.config/colab-cli/` directory (specifically `token.json`) is then shipped to Render as a mounted secret (Render "Secret Files" or an encrypted persistent disk), and the controller's CLI invocations read it non-interactively from then on — exactly the same operational pattern as deploying a `gcloud` service that uses a pre-generated ADC file. This satisfies the brief's "if a documented headless mechanism exists, use it; otherwise stop" instruction — it does exist.

**Caveats that must be documented for you, not silently swallowed:**
1. This is **your personal Google account's** credential, not a service account. The whole system's GPU access rides on your personal Colab entitlement/quota, and if Google forces a re-auth (password change, suspicious-activity flag, token revocation), the system goes dark until you manually re-run `colab auth` and re-upload the token to Render. There is no service-account equivalent for consumer Colab (only Colab Enterprise/Vertex AI has service-account-based auth — see §3).
2. Because it's a personal account behind an automated system, this project inherits your account's Colab usage history and risk profile — see "Policy risk" below.

Sources: [GitHub search results / repo auth docs](https://github.com/googlecolab/google-colab-cli), corroborated by the OOB-deprecation context from [Colab-Hacks issue #80](https://github.com/PradyumnaKrishna/Colab-Hacks/issues/80).

---

## 3. Colab Enterprise (Vertex AI) as an alternative — explicitly rejected

Google Cloud's **Colab Enterprise** (part of Vertex AI) does have a fully service-account-authenticated, ADC-based API and `gcloud` CLI:

```
gcloud colab runtime-templates create --display-name=... --accelerator-type=... --accelerator-count=...
gcloud colab runtimes create --display-name=... --runtime-template=...
```

with a matching REST API (`POST .../notebookRuntimeTemplates`). This is genuinely headless-friendly with no personal-account coupling.

**Why it doesn't fit this project:** it's a billed GCP resource (you pay for the underlying Compute Engine VM/accelerator), not "Google Colab's free GPU," which is the explicit goal stated in the brief ("without keeping my local PC running... using Google Colab's free GPU resources"). Using Colab Enterprise would solve the automation problem but silently abandon the "free" requirement. Documenting this rather than substituting it silently: if free-tier availability/reliability turns out to be a dead end in practice, Colab Enterprise is the documented fallback, at the cost of real GPU billing.

Sources: [Create a runtime in Colab Enterprise](https://docs.cloud.google.com/vertex-ai/docs/colab/create-runtime), [Create a runtime template](https://docs.cloud.google.com/vertex-ai/docs/colab/create-runtime-template), [Authenticate to Agent Platform](https://docs.cloud.google.com/colab/docs/authentication).

---

## 4. Runtime lifecycle: idle timeout, keep-alive, session persistence

From the CLI's own `docs/01_session_management.md`:

- **Keep-alive mechanism**: `colab new` spawns a **detached background daemon process on the CLI's host machine** (i.e., on Render, not inside the Colab VM) that pings `GET https://colab.research.google.com/tun/m/<endpoint>/keep-alive/` every 60 seconds with the session's bearer token. This is what prevents Colab's normal idle-eviction from killing the VM.
  - **This means the daemon must keep running on Render for as long as we want the session alive** — it is not a fire-and-forget "start it and Colab keeps it up forever" mechanism. Render's controller process must be a persistent service (which the brief already specifies), not a scale-to-zero function.
  - Safety cap: the daemon self-terminates after **24 hours** regardless, and also exits after two consecutive non-timeout 4xx errors.
- **Idle timeout** (from the official Colab FAQ): free tier sessions run for **up to ~12 hours** subject to availability/usage patterns even with keep-alive; Colab Pro+ extends this to ~24h. GPU/TPU type availability on free tier is explicitly called "heavily restricted" and varies over time — **`GPU_UNAVAILABLE` must be treated as a routine, expected outcome, not an edge case.**
- **Session persistence across controller restarts**: session bearer tokens and backend URLs are cached in `~/.config/colab-cli/sessions.json` on the CLI host. If Render restarts, as long as that file (and the OAuth token file) survive on a persistent volume/secret mount, `colab sessions` / `colab status -s NAME` can rediscover and reconnect to a still-running Colab VM without re-provisioning. This directly answers the brief's question about recovering state after a Render restart — the answer is "yes, if you persist `~/.config/colab-cli/`," which the controller must therefore treat as state to persist, not just the token.

**Known reliability bug**: [GitHub issue #14](https://github.com/googlecolab/google-colab-cli/issues/14) reports interactive sessions becoming unresponsive ~80 seconds after the terminal loses focus, with the client pruning the session as "stale." This is in the interactive TTY code path (`repl`/terminal focus), not the `exec`/`run` non-interactive path this project will use, but it's a concrete signal the CLI is early-stage software (shipped ~3 months before this document) and unattended long-running sessions should be treated as unreliable by design — reinforcing, not just satisfying, the brief's requirement for heartbeat timeouts and automatic worker restart.

Sources: [google-colab-cli docs/01_session_management.md](https://github.com/googlecolab/google-colab-cli/blob/main/docs/01_session_management.md), [docs/05_run_command.md](https://github.com/googlecolab/google-colab-cli/blob/main/docs/05_run_command.md), [Colab FAQ](https://research.google.com/colaboratory/faq.html), [issue #14](https://github.com/googlecolab/google-colab-cli/issues/14).

---

## 5. Networking: can Render reach a port on the Colab VM? Can the VM reach Render?

This is the single biggest correction to the brief's assumed architecture.

**Render → Colab VM (inbound to the VM): not possible, in any form.** There is no port-forwarding, no exposed localhost, no "Colab gives you a public URL for your service" mechanism in the official tooling. The *only* channel into a Colab VM is Google's own tunnel (`colab.research.google.com/tun/...`), and the only client authorized to use it is whoever holds the session's OAuth-derived bearer token — i.e., the CLI process itself. There is no generic reverse-proxy/ingress for arbitrary services running inside the VM. (The prior attempt already in this repo, `connect_colab.sh`/`server_colab.py`, worked around this exact limitation with a `cloudflared` tunnel started *from inside* the notebook — a third-party tunneling dependency the brief explicitly says to avoid "unless there is a strong reason.")

**Colab VM → Render (outbound from the VM): works normally.** Colab VMs have ordinary internet egress (this is how `pip install`, dataset/model downloads, etc. already work in any notebook), so code executed inside the VM can make outbound HTTPS calls to Render without any special setup.

**But here's the redesign this enables:** Render doesn't need the worker to call it back at all for the *control plane*, because Render already has a live, authenticated, synchronous RPC channel into the VM via `colab exec` — it's the CLI process itself, running on Render, that is the "connection" to Colab. Render can:
1. `colab new --gpu T4 --keep` once to provision and hold a session open.
2. `colab exec` a script that imports the model and defines a `generate(prompt) -> str` function in the kernel's global namespace — this is the "load" step, done once.
3. For every inference request, `colab exec` a small snippet that calls `generate(...)` and prints JSON to stdout, which the CLI subprocess captures and Render parses as the response.
4. `colab stop -s NAME` on idle timeout.

No inbound port on the VM, no outbound HTTP client/server code needed inside the VM, no worker-side registration or heartbeat-push protocol required for correctness — Render already knows the session is alive because it owns the CLI process and can poll `colab status`. **This is a strictly simpler and more robust design than the brief's Component 2 (worker self-registers, worker sends heartbeats, worker runs its own inference HTTP server)**, and it sidesteps a networking problem (exposing an HTTP server from inside Colab to Render) that turns out not to be solvable the way the brief assumed anyway.

This is a substantive deviation from the brief's stated design and is called out explicitly, with the alternative, in `docs/architecture.md` — including a documented fallback (worker-push over outbound HTTPS, "Design B") in case per-request `colab exec` latency/reliability proves inadequate once measured in Phase 4.

---

## 6. Policy / Terms of Service risk (read this before deploying)

The [Colab FAQ](https://research.google.com/colaboratory/faq.html) states, for **free managed runtimes**, that the following are disallowed without a paid plan:
- "remote control such as SSH shells, remote desktops"
- "bypassing the notebook UI to interact primarily via a web UI" (interpreted here as: interacting with the runtime other than through the notebook UI)
- running distributed computing workers

Read literally, `colab exec`/`colab ssh`/`colab console`/`colab run` — Google's own official CLI — *is* this category of usage. No source found (Google's blog post, the CLI's own docs, or third-party coverage including the security-focused Help Net Security piece) explicitly reconciles the CLI's existence with this FAQ clause. Two readings are both plausible:
- **(a)** The FAQ clause predates the CLI and targets the unofficial hacks that motivated it (Selenium-driven bots, SSH-over-tunnel shims like the pre-existing third-party `colabctl`/`colab-cli` PyPI packages found during this research) — and Google shipping and actively promoting an official CLI for "AI agents" is itself the update to accepted use.
- **(b)** The FAQ is simply stale/not yet reconciled with the new product, and heavy automated use could still be flagged by Colab's abuse-detection systems, which are known to produce false positives even for legitimate use — see [issue #6090](https://github.com/googlecolab/colabtools/issues/6090) ("Pro+ got banned due to incorrect automated ban system") and [issue #6035](https://github.com/googlecolab/colabtools/issues/6035) ("blocked... due to suspected abusive activity").

**This is not something I can resolve for you by reading more docs — it's a judgment call about risk tolerance on your own Google account.** What I can do, and have done in the design: minimize the automated-restart footprint (a sane default 10-minute idle timeout rather than aggressive churn, one session at a time via the startup lock, no parallel runtime creation), make the idle timeout and retry/backoff configurable so you can be conservative, and keep the whole Colab-driving layer isolated behind `ColabManager` so that if you ever decide free-tier automation isn't worth the account risk, swapping in Colab Enterprise (§3) or a different GPU provider only touches one module.

**Recommendation:** proceed, since Google is the one distributing and marketing this CLI for exactly this use case, but go in with eyes open that this is a ~3-month-old product and Colab's abuse detection has a history of false positives; don't point this system at an account you can't afford to have flagged.

---

## 7. Model-serving runtime on a free T4

Not deeply re-researched here since it's a standard, well-documented space, but worth stating the constraint this imposes: free-tier T4 gives ~16GB VRAM and shared/variable CPU and disk. For a 2–3B parameter model:
- `transformers` (+ `bitsandbytes` 4/8-bit or plain fp16) is the simplest, most reliable option and fits comfortably — recommended for the first end-to-end proof.
- `vllm` is heavier to install/initialize and is built for multi-request throughput/batching this single-user, single-GPU, exec-driven design doesn't need — brief explicitly warns against reaching for it by default, and research doesn't surface a reason to override that here.
- `llama.cpp`/`llama-cpp-python` (GGUF, quantized) is a good fallback if VRAM or install time on a fresh VM becomes the bottleneck once measured in Phase 4.

Decision: start with `transformers`, keep `InferenceEngine` as a real abstraction (per the brief) so swapping to `llama-cpp-python` later is a contained change.

---

## 8. Summary of corrections to the original brief

| Brief assumption | Research finding | Action |
|---|---|---|
| A Colab CLI with `new`/`exec`/`run`/`stop`/`--keep`/GPU flags exists | **Confirmed** — official, Google-shipped, Apache-2.0 | Use it as designed |
| Headless auth is possible | **Confirmed** — OAuth2 remote copy-paste flow, cacheable token | One-time human step, documented in `docs/colab-auth.md` |
| Render can reach a worker HTTP server on the Colab VM | **False** — no inbound path exists at all | Drive inference via `colab exec` RPC instead of an inbound HTTP call |
| Worker should run its own HTTP inference server and push registration/heartbeats to Render | **Unnecessary and unsupported by any real transport** given the above | Render's CLI process *is* the live connection; "registration" = successful model-load `exec`, "heartbeat" = Render polling `colab status`/a cheap `exec`. `WORKER_MODE=mock` still gets a real push-based HTTP worker for local dev, since that path has no networking constraint. |
| Free Colab GPU automation is uncontroversially within policy | **Ambiguous** — literal FAQ text conflicts with Google's own official CLI's purpose | Documented as an accepted risk, isolated behind `ColabManager`, kept configurable/conservative |
| Render restart loses all worker state | **Partially false** — session tokens are locally cached and sessions can be rediscovered | Persist `~/.config/colab-cli/` (not just an app-level DB) across controller restarts |

See `docs/architecture.md` for the resulting system design.

---

## 9. Phase 1 validation (live run, not simulated)

This machine already had a cached, working `colab auth` token from earlier work in this repo, so Phase 1 was run for real against the live CLI (v0.6.0) rather than left as an untested script — see `phase1_poc/run_poc.py`. Actual output from a real run on 2026-09-15:

```json
{
  "python_version": "3.13.15",
  "torch_version": "2.11.0+cu128",
  "cuda_available": true,
  "gpu_name": "Tesla T4",
  "gpu_count": 1,
  "gpu_total_memory_gb": 14.56,
  "nvidia_smi": "Tesla T4, 15360 MiB, 580.82.07"
}
```

Stage timings from that run: `gpu_provision_seconds: 15.1`, `exec_seconds: 8.3`, `total_seconds: 24.6` (create → run script → capture GPU info; runtime was then cleanly stopped via `colab stop`). This is a single sample, not a benchmark — GPU availability and provisioning time will vary — but it confirms the full lifecycle (`colab new` → `colab exec` → `colab stop`) works exactly as documented above, with no invented syntax, and that cold-start-to-first-exec on a free T4 can be well under a minute rather than the multi-minute worst case the brief anticipated. Model load time (Phase 4) will dominate actual cold-start latency, not CLI/provisioning overhead.

## 10. Phase 4-lite validation (real model, real GPU, real controller) — and two real bugs it found

With the full controller built (Design A), a live end-to-end run was done against a real T4 with `Qwen/Qwen2.5-0.5B-Instruct` through the actual `/v1/chat/completions` API, not a standalone script. This caught two real bugs that pure code review missed, both since fixed:

1. **String-escaping bug in the bootstrap templating.** `ColabWorkerDriver.start()` substituted a `json.dumps()`-encoded config directly into a template slot that was *already* wrapped in Python string quotes (`json.loads("__MODEL_CONFIG_JSON__")`), producing invalid Python (`json.loads("{"model_id": "...`) the moment the model name or any value contained a quote-adjacent character. The fix (`controller/services/colab_driver.py`) double-encodes: `json.dumps(json.dumps(config))`, then strips the outer quotes — the same pattern already used correctly for per-request payloads in `infer()`, just missed in `start()`. **Lesson: exec-driven RPC over a text CLI needs the same string-injection discipline as SQL — verified live, not just reasoned about.**
2. **Failed retries didn't clean up the partially-created session.** When the (buggy) first attempt's `colab exec` produced a traceback instead of the expected `WORKER_READY_JSON:` marker, the retry loop went straight to another `colab new -s <same name>` — which fails immediately because that session already exists, burning the remaining retry budget on a guaranteed failure instead of ever reaching the real problem. Fixed by calling `driver.stop()` best-effort before each retry (`controller/services/worker_manager.py`). Also fixed: `ColabError` now carries the failing script's actual stdout (truncated) instead of just "marker not found", so a MODEL_LOAD_FAILED-style error is diagnosable from the logged event instead of requiring a manual `colab log` session.

After both fixes, a clean run against the same real T4 produced:

| Stage | Time |
|---|---|
| Cold start (provision + upload + install transformers/accelerate + download + load 0.5B model + first inference) | 74.7s total |
| **Warm per-request latency** (`colab exec` round-trip for a short generation, model already resident in the kernel) | **2.2s** |

The warm-latency number is the one that mattered most for validating Design A: research.md §5 proposed driving inference via `colab exec` per request instead of a worker-side HTTP server, with the open risk (architecture.md) that CLI RPC overhead might make this too slow for interactive use. **2.2s per request is well within an acceptable range for a single-GPU, single-model interactive backend — Design B (worker-push HTTP/long-poll) is not needed and is not being built.** The response was also qualitatively correct ("What is 2+2? Answer in one word." → "Two."), confirming the chat-template rendering and generation path work end to end, not just that a process returned exit code 0.

Both live runs (Phase 1 and this one) left no orphaned sessions or processes — verified with `colab sessions` returning empty after each.
