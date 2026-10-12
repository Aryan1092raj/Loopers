"""Copy every collection from a local Qdrant to Qdrant Cloud: config, payload indexes and points.

Index locally, then run this to publish. Reads QDRANT_CLOUD_URL and QDRANT_CLOUD_API_KEY from RAG_DB/.env;
the source defaults to QDRANT_URL. Destination collections are recreated, so the cloud mirrors local.

    python src/push_qdrant.py [--src http://localhost:6333]
"""
import argparse
import os
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def push(src: QdrantClient, dst: QdrantClient, name: str, batch: int = 64) -> None:
    info = src.get_collection(name)
    p = info.config.params
    dst.recreate_collection(name, vectors_config=p.vectors, sparse_vectors_config=p.sparse_vectors,
                            quantization_config=info.config.quantization_config, on_disk_payload=p.on_disk_payload)
    for field, schema in (info.payload_schema or {}).items():
        dst.create_payload_index(name, field, field_schema=schema.data_type)
    offset = None
    while True:
        points, offset = src.scroll(name, limit=batch, offset=offset, with_payload=True, with_vectors=True)
        if points:
            dst.upsert(name, [models.PointStruct(id=x.id, vector=x.vector, payload=x.payload) for x in points])
        if offset is None:
            break
    n_src, n_dst = src.count(name, exact=True).count, dst.count(name, exact=True).count
    assert n_src == n_dst, f"{name}: {n_src} local points but {n_dst} in cloud"
    print(f"{name}: {n_dst} points")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.getenv("QDRANT_URL", "http://localhost:6333"))
    args = ap.parse_args()
    src = QdrantClient(url=args.src, timeout=60)
    dst = QdrantClient(url=os.environ["QDRANT_CLOUD_URL"], api_key=os.environ["QDRANT_CLOUD_API_KEY"], timeout=60)
    for c in src.get_collections().collections:
        push(src, dst, c.name)
