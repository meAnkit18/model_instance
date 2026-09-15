FROM python:3.12-slim

# `google-colab-cli` supports Linux/macOS only (docs/research.md section 1)
# -- Render's containers are Linux, so this is fine.
#
# The real root cause behind two earlier failed fixes (docs/research.md
# section 11): google-colab-cli declares an UNVERSIONED dependency on
# "jupyter-kernel-client" (`Requires-Dist: jupyter-kernel-client`, no
# pin). The public PyPI package of that name (by Datalayer) is a
# different, incompatible codebase at every version checked -- 0.8.0 is
# missing `JupyterSubprotocol`, 1.0.2 is missing `KernelClient`. The
# version that actually works is Google's own git FORK at
# github.com/googlecolab/jupyter-kernel-client, self-labeled "0.8.0" to
# track upstream's version number but carrying different code. This is
# only resolvable by pointing pip at that exact commit directly -- no
# version pin of the public package can ever produce it. Requires `git`
# in the image for pip to fetch it.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
    "jupyter-kernel-client @ git+https://github.com/googlecolab/jupyter-kernel-client.git@f18e982c3265df5e923aa9def101ab3fd737e139" \
    google-colab-cli==0.6.0

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
