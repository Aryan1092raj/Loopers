"""Page-image retrieval: every rendered page and slide gets two vectors in the `pages` Qdrant collection.

colsmol: ColSmol-500M token vectors of the page image (ColPali-style late interaction, MaxSim).
gemma:   one EmbeddingGemma 2 vector of the same image, in the same space as the text index's query vectors.

A query searches both and Qdrant fuses them with RRF. A hit only names a (source_id, page) pair; page_chunks()
turns it into the text and figure units already stored for that page, so locators still come from stored fields.
Both indexing (end of `pipeline.py embed`) and retrieval are on only when LOOPERS_VISUAL_RETRIEVAL=1.
"""
import os
import uuid
from functools import lru_cache

from config import COLLECTION, PAGES_DIR, PROCESSED

PAGES = "pages"
COLPALI_MODEL = os.getenv("COLPALI_MODEL", "vidore/colSmol-500M")
ENABLED = os.getenv("LOOPERS_VISUAL_RETRIEVAL") == "1"
PAGE_K = 4  # pages per query; their units join the reranker's candidate pool
_NS = uuid.UUID("6c9a1f00-0000-4000-8000-0000000000a9")


@lru_cache(maxsize=1)
def _colpali():
    import torch
    from sentence_transformers import MultiVectorEncoder

    bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    print(f"[pages] Loading {COLPALI_MODEL}...")
    return MultiVectorEncoder(COLPALI_MODEL, device="cpu",
                              model_kwargs={"torch_dtype": torch.bfloat16 if bf16 else torch.float32})


def _encode(images: list[str] | None = None, query: str | None = None) -> list[list[list[float]]]:
    """Token vectors per input. Like the reranker, the model sits in CPU memory and visits the GPU only while
    encoding, so the 6 GB card is not held between queries."""
    import torch

    model = _colpali()
    if torch.cuda.is_available():
        model.to("cuda")
    try:
        with torch.inference_mode():
            if query is not None:
                out = model.encode_query([query], normalize_embeddings=True)
            else:
                out = model.encode_document(images, batch_size=2, normalize_embeddings=True)
        return [t.float().cpu().tolist() for t in out]
    finally:
        if torch.cuda.is_available():
            model.to("cpu")
            torch.cuda.empty_cache()


def _point_id(source_id: str, n: int) -> str:
    return str(uuid.uuid5(_NS, f"{source_id}/{n}"))


def _ensure_collection(c, colsmol_dim: int, gemma_dim: int) -> None:
    from qdrant_client import models

    if c.collection_exists(PAGES):
        return
    c.create_collection(PAGES, vectors_config={
        "colsmol": models.VectorParams(
            size=colsmol_dim, distance=models.Distance.COSINE,
            multivector_config=models.MultiVectorConfig(comparator=models.MultiVectorComparator.MAX_SIM),
        ),
        "gemma": models.VectorParams(size=gemma_dim, distance=models.Distance.COSINE),
    })


def run(batch: int = 8) -> int:
    """Encode every rendered page not yet in the collection. Returns the number of pages added."""
    import store
    from embeddings import encode
    from qdrant_client import models

    c = store.client()
    pages = [(p.parent.name, int(p.stem), p) for p in sorted(PAGES_DIR.glob("*/*.webp")) if p.stem.isdigit()]
    if c.collection_exists(PAGES):
        have = {r.id for i in range(0, len(pages), 256)
                for r in c.retrieve(PAGES, [_point_id(s, n) for s, n, _ in pages[i:i + 256]])}
        pages = [x for x in pages if _point_id(x[0], x[1]) not in have]
    print(f"[pages] {len(pages)} new page images to index")
    for i in range(0, len(pages), batch):
        part = pages[i:i + batch]
        paths = [str(p) for _, _, p in part]
        multi = _encode(images=paths)
        dense = encode([{"text": "<|image|>", "image": [p]} for p in paths])
        _ensure_collection(c, len(multi[0][0]), len(dense[0]))
        c.upsert(PAGES, points=[
            models.PointStruct(
                id=_point_id(sid, n), vector={"colsmol": mv, "gemma": dv},
                payload={"source_id": sid, "page": n, "image": str(p.relative_to(PROCESSED))},
            )
            for (sid, n, p), mv, dv in zip(part, multi, dense)
        ])
    return len(pages)


def search(query: str, qvec: list[float], k: int = PAGE_K) -> list[dict]:
    """Top pages for a query: ColSmol MaxSim and EmbeddingGemma image similarity, RRF-fused."""
    import store
    from qdrant_client import models

    c = store.client()
    if not c.collection_exists(PAGES):
        return []
    res = c.query_points(
        PAGES,
        prefetch=[
            models.Prefetch(query=_encode(query=query)[0], using="colsmol", limit=k * 3),
            models.Prefetch(query=qvec, using="gemma", limit=k * 3),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=k, with_payload=True,
    )
    return [dict(p.payload or {}, score=p.score) for p in res.points]


def page_chunks(query: str, qvec: list[float], k: int = PAGE_K) -> list[dict]:
    """Stored text and figure units on the top pages, best match to the query first."""
    import store
    from qdrant_client import models

    hits = search(query, qvec, k)
    if not hits:
        return []
    on_pages = models.Filter(should=[
        models.Filter(must=[
            models.FieldCondition(key="source_id", match=models.MatchValue(value=h["source_id"])),
            models.Filter(should=[
                models.FieldCondition(key=f, match=models.MatchValue(value=h["page"])) for f in ("page_no", "slide_no")
            ]),
        ])
        for h in hits
    ])
    res = store.client().query_points(COLLECTION, query=qvec, using=store.DENSE_VECTOR_NAME,
                                      query_filter=on_pages, limit=k * 3, with_payload=True)
    return [dict(p.payload or {}, score=p.score, _via_page=True) for p in res.points]


if __name__ == "__main__":
    print(f"[pages] indexed {run()} pages")
