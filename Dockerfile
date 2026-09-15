FROM python:3.12-slim

# `google-colab-cli` supports Linux/macOS only (docs/research.md section 1)
# -- Render's containers are Linux, so this is fine.
RUN pip install --no-cache-dir google-colab-cli

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY controller ./controller
COPY worker ./worker
COPY requirements-worker.txt .
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# COLAB_CONFIG_DIR must be writable (the CLI updates sessions.json on
# every call) -- docker-entrypoint.sh copies the read-only Render Secret
# File (/etc/secrets/token.json) here on boot. See docs/colab-auth.md.
# Without an attached persistent disk this is ephemeral per-deploy, which
# is fine: the entrypoint re-copies the credential from /etc/secrets on
# every boot regardless.
ENV COLAB_CONFIG_DIR=/data/colab-cli
ENV STATE_FILE=/data/controller_state.json
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
