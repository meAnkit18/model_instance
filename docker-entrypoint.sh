#!/bin/sh
# Render mounts Secret Files read-only at /etc/secrets/<name> -- but the
# colab CLI needs to WRITE to its config dir (sessions.json gets updated
# on every `colab new`/`colab stop`). It also resolves that config dir
# relative to $HOME with no override flag/env var of its own (verified
# live -- docs/research.md section 11), which is why COLAB_HOME_DIR here
# must match controller/config.py's `colab_home_dir` / colab_manager.py's
# subprocess HOME override exactly. So: copy the credential into
# $COLAB_HOME_DIR/.config/colab-cli/ once at boot, per docs/colab-auth.md.
set -e

CONFIG_DIR="$COLAB_HOME_DIR/.config/colab-cli"
mkdir -p "$CONFIG_DIR"

if [ -f /etc/secrets/token.json ] && [ ! -f "$CONFIG_DIR/token.json" ]; then
  cp /etc/secrets/token.json "$CONFIG_DIR/token.json"
fi
if [ -f /etc/secrets/settings.json ] && [ ! -f "$CONFIG_DIR/settings.json" ]; then
  cp /etc/secrets/settings.json "$CONFIG_DIR/settings.json"
fi

exec uvicorn controller.main:app --host 0.0.0.0 --port 8000
