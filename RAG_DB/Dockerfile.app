# Loopers study-companion API (api.main:app) on CPU.
# Build context is RAG_DB/; Dockerfile.app.dockerignore applies to this build only.
# Runtime data (raw/, processed/, metadata/, output/) is mounted as volumes, vectors live in the qdrant service.
FROM python:3.12-slim-bookworm

# ffmpeg: audio/keyframe extraction for video. libgl1/libglib2.0-0: opencv (scenedetect, rapidocr).
# libgomp1: torch CPU runtime. curl: healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgl1 libglib2.0-0 libgomp1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU wheels first so requirements.txt does not pull the multi-GB CUDA build.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY templates ./templates

# config.ROOT resolves to /app. LOOPERS_NO_REEXEC stops config.py re-executing the process under uvicorn.
ENV LOOPERS_NO_REEXEC=1 \
    MEDIA_DIR=/app/processed/media \
    HF_HOME=/models \
    MPLBACKEND=Agg \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --retries=10 --start-period=120s \
    CMD curl -fsS -o /dev/null http://localhost:8000/api/status || exit 1

CMD ["uvicorn", "api.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
