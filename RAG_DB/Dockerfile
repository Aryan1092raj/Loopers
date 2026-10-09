# Content Studio (RAG) — generation service, CPU image.
#
# Serves the Next.js app's /api/rag/* proxy: given a topic it retrieves from the
# baked vector corpus and generates slides (.pptx + PDF), notes, or assignments.
# Listens on :8000 (stdlib http.server, one job at a time).
#
# WHAT THIS IMAGE DOES NOT DO: ingest new textbooks. OCR/layout (PaddleOCR,
# MinerU, Docling) and GPU embedding are intentionally excluded to keep the image
# small and CPU-only. To add corpus, run the full pipeline offline (a dev/GPU
# machine, requirements.txt) and commit the refreshed qdrant_db/. See
# docs/deployment-guide, "Deploying Content Studio (RAG)".
FROM python:3.11-slim-bookworm

# System deps:
#   - TeX Live (same set as the web app) → xelatex for the Beamer/notes PDFs
#   - libgomp1 → required by torch's CPU runtime
#   - curl/ca-certificates → healthcheck + outbound HTTPS to the LLM APIs
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates libgomp1 \
    texlive-latex-base texlive-latex-recommended texlive-latex-extra \
    texlive-fonts-recommended texlive-science texlive-pictures \
    texlive-xetex fonts-noto-core fonts-texgyre \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only torch FIRST, from PyTorch's CPU wheel index, so the FlagEmbedding
# sentence-transformers install below reuses it instead of pulling the multi-GB CUDA build.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.docker.txt ./
RUN pip install --no-cache-dir -r requirements.docker.txt

# App code + the LaTeX/schema templates + the prebuilt embedded vector corpus.
# config.ROOT resolves to /app, so ROOT/qdrant_db and ROOT/templates line up.
COPY src ./src
COPY templates ./templates
COPY qdrant_db ./qdrant_db

# Embedded Qdrant (empty QDRANT_URL = no server), CPU, headless matplotlib,
# HF model cache on a mounted volume, unbuffered logs for `docker logs`.
ENV QDRANT_URL="" \
    USE_GPU_OCR=0 \
    MPLBACKEND=Agg \
    HF_HOME=/models \
    PYTHONUNBUFFERED=1 \
    LOOPERS_NO_REEXEC=1

EXPOSE 8000

# The EmbeddingGemma 2 + reranker models download to HF_HOME on the first
# generation and persist in the mounted volume. Start the API.
CMD ["uvicorn", "api.main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8000"]
