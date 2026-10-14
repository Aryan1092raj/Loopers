import os
import logging
import threading
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

import worker_client
from api import sync
from api.routes import router
from api.worker import router as worker_router, require_key
from config import PAGES_DIR, RAW
from core.db import init_db
from worker_client import WorkerOffline

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Loopers Multimodal API")
init_db()
app.include_router(router)
app.include_router(worker_router)
app.middleware("http")(require_key)


@app.exception_handler(WorkerOffline)
async def worker_offline(request: Request, exc: WorkerOffline):
    return JSONResponse({"detail": str(exc)}, status_code=503)


def _try_sync() -> None:
    if not worker_client.WORKER_URL:
        return
    try:
        sync.sync_knowledge()
    except Exception as e:  # the worker may be asleep; the next finished job syncs again
        logger.warning("startup knowledge sync skipped: %s", e)


# In a thread so a sleeping worker cannot delay Render's health check.
app.add_event_handler("startup", lambda: threading.Thread(target=_try_sync, daemon=True).start())

# CORS configuration (Updated per TRD spec)
cors_origins_env = os.getenv("API_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000")
allow_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "../../processed/media")).resolve()

# Page images (A1): <source_id>/<n>.webp, written by ingest/pages.py under processed/media/pages.
app.mount("/media/pages", StaticFiles(directory=PAGES_DIR, check_dir=False), name="pages")

@app.get("/api/status")
def get_status():
    return {"ok": True}

# Keepalive target for the cron that stops Render's free tier sleeping. HEAD too: uptime pingers send it.
# ponytail: no DB or Qdrant check, so a ping stays cheap; add one if this becomes a readiness probe.
@app.api_route("/api/health", methods=["GET", "HEAD"], include_in_schema=False)  # GET+HEAD share an operationId whose pick varies per process
def health():
    return {"ok": True}

@app.get("/media/video/{file_path:path}")
def stream_video(file_path: str, request: Request):
    """Serves video files with HTTP Range support."""
    
    # Uploaded lectures live under raw/<subject>/ (the seek_url carries that relative path); keep the old video dir too.
    # Security: each candidate must stay inside its own root (no directory traversal).
    for root in (MEDIA_DIR / "video", RAW.resolve()):
        video_path = (root / file_path).resolve()
        if video_path.is_file() and video_path.is_relative_to(root.resolve()):
            break
    else:
        raise HTTPException(status_code=404, detail="Video not found")
    
    file_size = video_path.stat().st_size
    range_header = request.headers.get("Range")
    
    if not range_header:
        return StreamingResponse(
            open(video_path, "rb"),
            media_type="video/mp4",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size)
            }
        )
    
    # Parse Range header
    try:
        byte_range = range_header.replace("bytes=", "").split("-")
        start = int(byte_range[0])
        end = int(byte_range[1]) if byte_range[1] else file_size - 1
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Invalid Range header")
    
    if start >= file_size or end >= file_size or start > end:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail="Requested range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"}
        )
    
    chunk_size = end - start + 1
    
    def iter_file():
        with open(video_path, "rb") as f:
            f.seek(start)
            yield f.read(chunk_size)
    
    return StreamingResponse(
        iter_file(),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(chunk_size),
            "Content-Type": "video/mp4",
        }
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
