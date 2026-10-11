# Local Whisper transcription server (OpenAI-compatible /v1/audio/transcriptions), CPU by default.
FROM python:3.12-slim-bookworm
WORKDIR /app
# EXTRA_PIP: docker-compose.gpu.yml adds the CUDA 12 cuBLAS/cuDNN wheels that CTranslate2 needs on a GPU.
ARG EXTRA_PIP=""
# av 19 dropped the metadata_errors argument faster-whisper 1.2 passes to av.open.
RUN pip install --no-cache-dir faster-whisper "av<19" fastapi "uvicorn[standard]" python-multipart ${EXTRA_PIP}
COPY server.py ./
ENV HF_HOME=/models \
    WHISPER_DEVICE=cpu \
    WHISPER_COMPUTE_TYPE=int8 \
    WHISPER_MODEL=base \
    PYTHONUNBUFFERED=1
EXPOSE 8300
HEALTHCHECK --interval=15s --timeout=5s --retries=20 --start-period=60s \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8300/health')" || exit 1
CMD ["python", "server.py", "--port", "8300"]
