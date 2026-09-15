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

**Important, found by live testing, not just reasoning about it (see
docs/research.md section 11): the `colab` CLI does not read a
`COLAB_CONFIG_DIR`-style environment variable at all.** It resolves
`~/.config/colab-cli/` and `~/.colab-cli-oauth-config.json` strictly
relative to `$HOME`, with no CLI flag or env var to redirect that. The
first deploy of this project got this wrong (invented a
`COLAB_CONFIG_DIR` setting that the binary silently ignored, so it fell
through to an interactive login prompt inside the container and failed).
The fix: `colab_manager.py` overrides `HOME` itself for every CLI
subprocess call, to `settings.colab_home_dir` — so what actually matters
is getting your credential onto that directory's `.config/colab-cli/`
subpath, not an arbitrarily-named config directory.

Render's controller needs `$COLAB_HOME_DIR/.config/colab-cli/token.json`
to exist inside the running container. Two supported ways to get it
there, in order of preference:

1. **Render Secret Files** (Dashboard → your service → Environment →
   Secret Files): upload `token.json` (and `settings.json` if present) —
   Render always mounts these read-only at `/etc/secrets/<name>`, never at
   a path you choose. Since the CLI needs to *write* to its config dir
   (`sessions.json` updates on every call), `docker-entrypoint.sh` copies
   `/etc/secrets/token.json` into `$COLAB_HOME_DIR/.config/colab-cli/` on
   every container boot. Set `COLAB_HOME_DIR` to a writable path (e.g.
   `/data`) — see `Dockerfile`.
2. **Render persistent disk**: attach a disk at `/data` (`COLAB_HOME_DIR=/data`)
   so `sessions.json` also persists *across restarts*, not just across the
   entrypoint's boot-time copy — this is what makes the "rediscover a
   still-running Colab session after a Render restart" behavior in
   `docs/architecture.md` actually work. Without a disk (e.g. Render's
   free plan), the entrypoint still re-copies the credential from the
   Secret File on every boot, so auth keeps working — you just lose the
   session-rediscovery benefit.

Either way, the `colab` binary must also be present in the container —
handled by the `Dockerfile` (`RUN pip install google-colab-cli`).

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
