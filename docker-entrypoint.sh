#!/bin/sh
# Render mounts Secret Files read-only at /etc/secrets/<name> -- but the
# colab CLI needs to WRITE to its config dir (sessions.json gets updated
# on every `colab new`/`colab stop`). So copy the credential into a
# writable location once at boot, per docs/colab-auth.md.
set -e

mkdir -p "$COLAB_CONFIG_DIR"

if [ -f /etc/secrets/token.json ] && [ ! -f "$COLAB_CONFIG_DIR/token.json" ]; then
  cp /etc/secrets/token.json "$COLAB_CONFIG_DIR/token.json"
fi
if [ -f /etc/secrets/settings.json ] && [ ! -f "$COLAB_CONFIG_DIR/settings.json" ]; then
  cp /etc/secrets/settings.json "$COLAB_CONFIG_DIR/settings.json"
fi

exec uvicorn controller.main:app --host 0.0.0.0 --port 8000
