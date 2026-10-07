"""BGE-M3 embeddings — dense + sparse (GPU-accelerated, RTX 4050).

BGE-M3 can output three vector types in a single forward pass:
  dense_vecs  — 1024-dim dense semantic vectors  (used for semantic retrieval)
  lexical_weights — sparse BM25-style lexical vectors (used for keyword retrieval)
  colbert_vecs — token-level multi-vectors (optional, not used by default)

encode() returns only dense vectors (backwards-compatible).
encode_hybrid() returns both dense and sparse for hybrid Qdrant upserts.

Auto-detects CUDA: fp16 + batch_size=128 on GPU, fp32 + batch_size=32 on CPU.
max_length capped at 512 tokens — sufficient for ~350-word chunks.
"""
from functools import lru_cache

from config import EMBED_MODEL


@lru_cache(maxsize=1)
def _model():
    import torch
    import transformers
    from config import EMBED_MODEL

    transformers.logging.set_verbosity_error()

    use_fp16 = torch.cuda.is_available()
    device = "cuda" if use_fp16 else "cpu"

    if "bge-m3" in EMBED_MODEL.lower():
        from FlagEmbedding import BGEM3FlagModel
        print(f"[embeddings] Loading BGE-M3 on {device.upper()}"
              f"{' (fp16)' if use_fp16 else ' (fp32)'}...")
        return BGEM3FlagModel(EMBED_MODEL, use_fp16=use_fp16)
    elif "embeddinggemma-2" in EMBED_MODEL.lower():
        from sentence_transformers import SentenceTransformer
        # bf16 only: fp16 gives silent NaNs. Needs transformers>=5, which FlagEmbedding (pinned 4.44.2) rejects.
        dtype = torch.bfloat16 if use_fp16 and torch.cuda.is_bf16_supported() else torch.float32
        print(f"[embeddings] Loading EmbeddingGemma 2 on {device.upper()} ({str(dtype).split('.')[-1]})...")
        return SentenceTransformer(EMBED_MODEL, device=device, model_kwargs={"torch_dtype": dtype})
    else:
        from sentence_transformers import SentenceTransformer
        print(f"[embeddings] Loading SentenceTransformer '{EMBED_MODEL}' on {device.upper()}...")
        return SentenceTransformer(EMBED_MODEL, device=device)


def encode(texts, is_query: bool = False) -> list[list[float]]:
    """Encode str or list[str] -> list of dense vectors (plain Python lists).

    EmbeddingGemma 2 also takes {"text": "... <|image|>", "image": [path]} items and uses
    is_query to pick its SearchQuery/Document prompt; BGE-M3 ignores is_query.
    """
    import torch
    from config import EMBED_MODEL

    if isinstance(texts, str):
        texts = [texts]

    batch_size = 128 if torch.cuda.is_available() else 32
    model = _model()

    if "bge-m3" in EMBED_MODEL.lower():
        out = model.encode(texts, batch_size=batch_size, max_length=512)
        return [v.tolist() for v in out["dense_vecs"]]
    elif "embeddinggemma-2" in EMBED_MODEL.lower():
        out = model.encode(texts, batch_size=8, show_progress_bar=False, normalize_embeddings=True,
                           prompt_name="SearchQuery" if is_query else "Document")
        return [v.tolist() for v in out]
    else:
        out = model.encode(texts, batch_size=batch_size, show_progress_bar=False)
        return [v.tolist() for v in out]


def encode_hybrid(texts, is_query: bool = False) -> tuple[list[list[float]], list[dict]]:
    """Encode texts and return (dense_vectors, sparse_vectors).

    sparse_vectors: list of {"indices": [...], "values": [...]} dicts
    suitable for direct Qdrant SparseVector upsert.

    Falls back to (dense_vectors, None) for non-BGE-M3 models. Pass is_query=True at retrieval time.
    """
    import torch
    from config import EMBED_MODEL

    if isinstance(texts, str):
        texts = [texts]

    batch_size = 128 if torch.cuda.is_available() else 32
    model = _model()

    if "bge-m3" in EMBED_MODEL.lower():
        out = model.encode(
            texts,
            batch_size=batch_size,
            max_length=512,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        dense = [v.tolist() for v in out["dense_vecs"]]

        # lexical_weights is a list of dicts: {token_id: weight, ...}
        sparse = []
        for lw in out["lexical_weights"]:
            indices = [int(k) for k in lw.keys()]
            values = [float(v) for v in lw.values()]
            sparse.append({"indices": indices, "values": values})

        return dense, sparse
    else:
        dense = encode(texts, is_query)
        return dense, None  # type: ignore[return-value]


def get_vector_size() -> int:
    from config import EMBED_MODEL
    if "bge-m3" in EMBED_MODEL.lower():
        return 1024
    model = _model()
    if hasattr(model, "get_sentence_embedding_dimension"):
        return model.get_sentence_embedding_dimension()
    return 384
