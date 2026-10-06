import os
import logging
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import uvicorn

from api.routes import router
from config import PAGES_DIR
from core.db import init_db

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Loopers Multimodal API")
init_db()
app.include_router(router)

# CORS configuration (Updated per TRD spec)
cors_origins_env = os.getenv("API_CORS_ORIGINS", "http://localhost:3000")
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

@app.get("/", include_in_schema=False)
def console():
    """Neobrutalist study console: ingest, tutor with citations, concept map, learner and quiz (static, no build step)."""
    return FileResponse(Path(__file__).parent / "static" / "index.html")

@app.get("/api/status")
def get_status():
    return {"ok": True}

@app.get("/media/video/{file_path:path}")
def stream_video(file_path: str, request: Request):
    """Serves video files with HTTP Range support."""
    
    # Security: Prevent directory traversal
    video_path = (MEDIA_DIR / "video" / file_path).resolve()
    if not video_path.is_file() or not str(video_path).startswith(str(MEDIA_DIR)):
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
