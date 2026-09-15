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

# COLAB_CONFIG_DIR should point at a mounted secret/disk path in production
# -- see docs/colab-auth.md. This default is only used for WORKER_MODE=mock
# local/dev images where no real Colab auth is needed.
ENV COLAB_CONFIG_DIR=/data/colab-cli
ENV STATE_FILE=/data/controller_state.json
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "controller.main:app", "--host", "0.0.0.0", "--port", "8000"]
