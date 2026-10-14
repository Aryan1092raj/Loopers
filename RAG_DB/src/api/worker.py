"""GPU-worker routes, served by the Lightning process only (IS_WORKER=1). Render calls these; the browser never does."""
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from core.db import get_connection

router = APIRouter(prefix="/worker", include_in_schema=False)
# Rebuilt as a whole by the concepts stage, so Render copies all of them at once (api/sync.py).
KNOWLEDGE_TABLES = ["sources", "units", "concepts", "concept_edges", "unit_concepts"]


async def require_key(request: Request, call_next):
    key = os.getenv("WORKER_KEY", "")
    open_path = request.url.path == "/api/health" or request.method == "OPTIONS"
    if os.getenv("IS_WORKER") == "1" and key and not open_path and request.headers.get("X-Worker-Key") != key:
        return JSONResponse({"detail": "bad worker key"}, status_code=401)
    return await call_next(request)


class EmbedReq(BaseModel):
    texts: list[str]
    is_query: bool = False


class RerankReq(BaseModel):
    query: str
    texts: list[str]


@router.post("/embed")
def embed(req: EmbedReq):
    from embeddings import encode_hybrid

    dense, sparse = encode_hybrid(req.texts, req.is_query)
    return {"dense": dense, "sparse": sparse}


@router.post("/rerank")
def rerank(req: RerankReq):
    from generate import rerank_scores

    return {"scores": rerank_scores(req.query, req.texts)}


@router.get("/export")
def export():
    conn = get_connection()
    try:
        out = {}
        for table in KNOWLEDGE_TABLES:
            cur = conn.execute(f"SELECT * FROM {table}")
            out[table] = {"columns": [c[0] for c in cur.description], "rows": [list(r) for r in cur.fetchall()]}
        return out
    finally:
        conn.close()
