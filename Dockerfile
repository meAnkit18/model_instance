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

# COLAB_HOME_DIR must be writable (the CLI updates sessions.json on every
# call, and resolves its token cache relative to $HOME with no override
# flag of its own -- docs/research.md section 11). docker-entrypoint.sh
# copies the read-only Render Secret File (/etc/secrets/token.json) into
# $COLAB_HOME_DIR/.config/colab-cli/ on boot; colab_manager.py sets HOME=
# $COLAB_HOME_DIR for every CLI subprocess call. See docs/colab-auth.md.
# Without an attached persistent disk this is ephemeral per deploy, which
# is fine: the entrypoint re-copies the credential from /etc/secrets on
# every boot regardless.
ENV COLAB_HOME_DIR=/data
ENV STATE_FILE=/data/controller_state.json
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["./docker-entrypoint.sh"]
