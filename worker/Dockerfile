ARG PYTHON_IMAGE=python:3.11-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89
FROM ${PYTHON_IMAGE}
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HF_HUB_DISABLE_TELEMETRY=1 MODEL_CACHE_DIR=/data/models
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
ENV HF_HOME=/data/models/huggingface HF_XET_CACHE=/data/models/xet
RUN mkdir -p /data/media /data/models && chown -R 10001:10001 /data
COPY . /app/worker
USER 10001:10001
ENTRYPOINT ["python", "-m", "worker.supervisor"]
