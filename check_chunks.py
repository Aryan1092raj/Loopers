import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
import generate
import store
from embeddings import encode_hybrid

query = "gaussian elimination"
dense_vecs, sparse_vecs = encode_hybrid(query)
qvec = dense_vecs[0]
qsparse = sparse_vecs[0] if sparse_vecs else None
broad = store.query(store.client(), qvec, k=10, subject="mathematics", sparse_vector=qsparse)
for idx, chunk in enumerate(broad, 1):
    print(f"\n--- CHUNK {idx} (Score: {chunk.get('score', 0):.4f}) ---")
    print(f"Heading: {chunk.get('heading')}")
    text = chunk.get('text', '')
    print(text[:300])
