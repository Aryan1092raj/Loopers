"""Qdrant local vector store — hybrid dense+sparse with scalar quantization.

Supports both embedded (default, path=qdrant_db/) and server mode (set QDRANT_URL).
Server mode: set QDRANT_URL=http://localhost:6333 in .env (see docker-compose.qdrant.yml).
Embedded mode: single-process lock, suited to sequential CLI usage.

HYBRID SEARCH (Phase 1+2):
  Each point stores both a dense BGE-M3 vector (1024-dim cosine) and a sparse
  BM25 lexical vector.  Retrieval uses Qdrant's native Prefetch API to execute
  both sub-queries inside the DB, then fuses with Reciprocal Rank Fusion (RRF)
  — no Python-side merge latency.

SCALAR QUANTIZATION (Phase 2):
  INT8 quantization reduces the dense index memory by ~75% with <1% recall loss.
  Payload is stored on NVMe (on_disk=True) so Qdrant doesn't buffer large
  text strings in RAM.

Payload indexes (_try_create_payload_indexes) are active in server mode — O(log n)
filtered queries. In embedded mode they silently no-op (linear scan fallback).
"""
import uuid

from config import QDRANT_DIR, COLLECTION, VECTOR_SIZE, RRF_K, SPARSE_K, DENSE_K

_NS = uuid.UUID("6c9a1f00-0000-4000-8000-000000000000")

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"


def client():
    from qdrant_client import QdrantClient
    import os
    import logging

    url = os.getenv("QDRANT_URL")  # e.g. "http://localhost:6333" for server mode
    if url:
        api_key = os.getenv("QDRANT_API_KEY")
        try:
            c = QdrantClient(url=url, api_key=api_key or None, timeout=5.0)
            c.get_collections()
            logging.info(f"Connected to Qdrant server at {url}")
            return c
        except Exception as e:
            logging.warning(f"Qdrant server at {url} unreachable, falling back to embedded: {e}")
    return QdrantClient(path=str(QDRANT_DIR))


def _point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(_NS, chunk_id))


def reset_collection(c) -> None:
    from qdrant_client import models

    if c.collection_exists(COLLECTION):
        c.delete_collection(COLLECTION)

    c.create_collection(
        COLLECTION,
        vectors_config={
            DENSE_VECTOR_NAME: models.VectorParams(
                size=VECTOR_SIZE,
                distance=models.Distance.COSINE,
                on_disk=True,  # keeps dense index off RAM during payload retrieval
            ),
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: models.SparseVectorParams(
                index=models.SparseIndexParams(on_disk=True),
            ),
        },
        quantization_config=models.ScalarQuantization(
            scalar=models.ScalarQuantizationConfig(
                type=models.ScalarType.INT8,
                quantile=0.99,
                always_ram=True,  # keep quantized index in RAM; payloads on disk
            ),
        ),
        on_disk_payload=True,  # payload text/metadata stored on NVMe, not RAM
    )
    _try_create_payload_indexes(c)


def _try_create_payload_indexes(c) -> None:
    """Attempt keyword indexes on filter-heavy fields.

    Silently ignored in embedded mode. In server mode these make filtered
    queries O(log n) instead of a full scan — critical at 1M+ chunks.
    """
    from qdrant_client import models
    import logging

    keyword = ("doc", "kind", "subject", "doc_type", "source_id", "source_type", "topic_id", "concept_ids", "lang")
    for field, schema in [(f, models.PayloadSchemaType.KEYWORD) for f in keyword] + [
        (f, models.PayloadSchemaType.INTEGER) for f in ("page_no", "slide_no")
    ]:
        try:
            c.create_payload_index(collection_name=COLLECTION, field_name=field, field_schema=schema)
        except Exception:
            pass


def ensure_indexes(c) -> None:
    if c.collection_exists(COLLECTION):
        _try_create_payload_indexes(c)


def _has_hybrid_schema(c) -> bool:
    """Return True if the collection uses named vectors (hybrid schema).

    Checks vectors_config type: dict = named (new schema), VectorParams = unnamed (legacy).
    Avoids accessing sparse_vectors_config which is absent on the local CollectionParams object
    in qdrant-client 1.12.x embedded mode.
    """
    if not c.collection_exists(COLLECTION):
        return False
    try:
        info = c.get_collection(COLLECTION)
        return isinstance(info.config.params.vectors, dict)
    except Exception:
        return False


def upsert(
    c,
    ids: list[str],
    dense_vectors: list[list[float]],
    payloads: list[dict],
    sparse_vectors: list[dict] | None = None,
) -> None:
    """Upsert points with dense (and optionally sparse) vectors.

    sparse_vectors: list of {"indices": [...], "values": [...]} dicts.
    If None, only dense vectors are stored (fallback for non-BGE-M3 models).
    """
    from qdrant_client import models

    named_schema = _has_hybrid_schema(c)
    has_sparse = sparse_vectors is not None

    points = []
    for idx, (chunk_id, dvec, payload) in enumerate(zip(ids, dense_vectors, payloads)):
        if named_schema:
            # New schema: always use named vector dict
            vector: dict | list = {DENSE_VECTOR_NAME: dvec}
            if has_sparse:
                svec = sparse_vectors[idx]  # type: ignore[index]
                if svec and svec.get("indices"):
                    vector[SPARSE_VECTOR_NAME] = models.SparseVector(
                        indices=svec["indices"],
                        values=svec["values"],
                    )
        else:
            # Legacy schema: unnamed dense vector
            vector = dvec

        points.append(
            models.PointStruct(id=_point_id(chunk_id), vector=vector, payload=payload)
        )

    batch = 512
    for start in range(0, len(points), batch):
        c.upsert(collection_name=COLLECTION, points=points[start: start + batch])


def delete_by_doc(c, doc: str) -> None:
    from qdrant_client import models

    c.delete(
        collection_name=COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="doc",
                        match=models.MatchValue(value=doc),
                    )
                ]
            )
        ),
    )


def delete_by_kind(c, kind: str) -> None:
    from qdrant_client import models

    c.delete(
        collection_name=COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="kind",
                        match=models.MatchValue(value=kind),
                    )
                ]
            )
        ),
    )


def query(
    c,
    vector: list[float],
    k: int,
    subject: str | None = None,
    kind: str | None = None,
    sparse_vector: dict | None = None,
    math_boost: bool = True,
) -> list[dict]:
    """Hybrid search: RRF-fuse dense + sparse sub-queries via Qdrant Prefetch API.

    Falls back to dense-only if the collection doesn't have the sparse schema
    or if no sparse_vector is provided.

    Args:
        vector:        Dense query vector (1024-dim).
        k:             Final number of results to return after RRF fusion.
        subject:       Optional Qdrant payload filter on 'subject' field.
        kind:          Optional Qdrant payload filter on 'kind' field.
        sparse_vector: {"indices": [...], "values": [...]} BM25 sparse vector.
        math_boost:    Re-sort has_math chunks first (notes and decks). The tutor passes False to keep RRF order.
    """
    from qdrant_client import models

    conditions = []
    if subject:
        if isinstance(subject, str):
            subject_list = [subject]
            if subject != "general":
                subject_list.append("general")
        else:
            subject_list = list(subject)
        conditions.append(
            models.FieldCondition(key="subject", match=models.MatchAny(any=subject_list))
        )
    if kind:
        conditions.append(
            models.FieldCondition(key="kind", match=models.MatchValue(value=kind))
        )
    query_filter = models.Filter(must=conditions) if conditions else None

    named_schema = _has_hybrid_schema(c)
    use_hybrid = sparse_vector is not None and named_schema

    if use_hybrid:
        # Hybrid: Prefetch both sub-queries, fuse with RRF inside Qdrant
        res = c.query_points(
            collection_name=COLLECTION,
            prefetch=[
                models.Prefetch(
                    query=vector,
                    using=DENSE_VECTOR_NAME,
                    limit=DENSE_K,
                    filter=query_filter,
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=sparse_vector["indices"],
                        values=sparse_vector["values"],
                    ),
                    using=SPARSE_VECTOR_NAME,
                    limit=SPARSE_K,
                    filter=query_filter,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=k,
            with_payload=True,
        )
    else:
        # Dense-only: specify using="dense" on named-schema, omit on legacy unnamed schema
        kwargs: dict = dict(
            collection_name=COLLECTION,
            query=vector,
            limit=k,
            with_payload=True,
            query_filter=query_filter,
        )
        if named_schema:
            kwargs["using"] = DENSE_VECTOR_NAME
        res = c.query_points(**kwargs)

    out = []
    for p in res.points:
        payload = dict(p.payload or {})
        payload["score"] = p.score
        out.append(payload)
    
    if math_boost:
        # Boost math chunks: sort by has_math desc, then score desc
        out.sort(key=lambda x: (0 if x.get("has_math") else 1, -x.get("score", 0)))
    return out


def count(c) -> int:
    if not c.collection_exists(COLLECTION):
        return 0
    return c.count(COLLECTION).count
