# Colab CLI authentication

Read `docs/research.md` section 2 first for the underlying research. This
document is the operational how-to.

## Why this can't be automated

`colab auth` uses a "remote copy-paste" OAuth2 flow: the CLI prints a URL,
a human opens it in a browser, signs in to their Google account, and pastes
a code back into the terminal. There is no service-account or API-key
equivalent for consumer Colab (only Colab Enterprise on Vertex AI has that
— see research.md §3, and it isn't "free Colab GPU"). This is a hard,
documented constraint, not a shortcut we chose not to take: **a human must
run this once, interactively.** No workaround (browser-cookie scraping,
headless Selenium login, etc.) is used or acceptable here.

## One-time setup (do this yourself, not from Render)

On your own machine (or this dev sandbox), with `google-colab-cli`
installed:

```bash
uv tool install google-colab-cli   # or: pip install google-colab-cli
colab auth
```

Follow the printed URL, sign in, paste back the code. This writes:

```
~/.config/colab-cli/token.json      <- the credential Render needs
~/.config/colab-cli/settings.json
~/.config/colab-cli/sessions.json   <- session cache, starts empty
```

**Never commit any of these files to git, ever, in any form** (`.gitignore`
in this repo already excludes `.config/`, `*.token.json`, and `data/`).

## Getting the credential onto Render

Render's controller needs `~/.config/colab-cli/` to exist inside the
running container, pointed to by `COLAB_CONFIG_DIR` (see `.env.example`).
Two supported ways to do this on Render, in order of preference:

1. **Render Secret Files** (Dashboard → your service → Environment →
   Secret Files): upload `token.json` and `settings.json` as secret files
   mounted at `/data/colab-cli/token.json` and
   `/data/colab-cli/settings.json`, and set `COLAB_CONFIG_DIR=/data/colab-cli`.
   Simple, but a redeploy that changes secret files requires re-uploading.
2. **Render persistent disk**: attach a disk at `/data`, `COLAB_CONFIG_DIR=/data/colab-cli`,
   and copy your local `~/.config/colab-cli/` contents onto it once (e.g.
   via `render ssh` or a one-off deploy hook). This also lets
   `sessions.json` persist across restarts, which is what makes the
   "rediscover a still-running Colab session after a Render restart"
   behavior in `docs/architecture.md` actually work.

Either way, the `colab` binary must also be present in the container —
handled by the `Dockerfile` (`RUN pip install google-colab-cli` or `uv tool
install`).

## Verifying it works

After deploying, `colab sessions` run by the controller should succeed
(no auth error) — check via `GET /status`, which will show `worker.state`
transition correctly instead of every request failing with a
`colab_cli_invoke` auth error in the logs.

## What happens when the token expires or is revoked

The whole system goes dark: every `colab new`/`colab exec` call will start
failing (logged as `startup_failed`/`colab_cli_invoke` errors, surfaced to
clients as `503`). There is no automatic recovery — a human must re-run
`colab auth` and re-upload the refreshed `token.json`. This is a real
operational dependency on your personal Google account (research.md §2);
treat "Colab auth broke" as a page-a-human event, not something the
controller can self-heal.

## Risk notes (see research.md §6 for the full discussion)

- This ties the entire system to **your personal Google account's** Colab
  entitlement and usage history, not a service account.
- Colab's abuse-detection system has a documented history of false
  positives on legitimate paid/free accounts. Keep `WORKER_IDLE_TIMEOUT_SECONDS`
  reasonably high (don't thrash restarts) and don't point this at an
  account you can't afford to have flagged, especially at first.
