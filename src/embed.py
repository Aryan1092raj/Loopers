"""Phase 4 — Chunk + embed into Qdrant (incremental, GPU-accelerated).

Chunking is section- and paragraph-aware (not blind fixed cuts): markdown is
split by heading, each section split into paragraphs, then paragraphs are packed
up to a word budget with a small carried overlap so answers don't lose context.
Image labels (caption + keywords) become their own chunks so figures are
retrievable. Vectors come from BGE-M3 on GPU; storage is Qdrant local.

INCREMENTAL MODE (default):
  Each processed .md file and the labels.json are sha256-hashed. If the hash
  hasn't changed since the last embed run, that file's chunks are skipped.
  Only new/changed files are re-embedded and upserted. This makes re-runs after
  adding a few PDFs very fast even with 1000s of documents.

FULL REBUILD:
  Pass force_full=True (or --full from CLI) to wipe the collection and re-embed
  everything from scratch.
"""
import hashlib
import json
import re
from pathlib import Path

from config import PROCESSED, COLLECTION, EMBED_STATE, METADATA
from label import load_labels

# Set CLAF_TAGGING=1 in .env to enable LLM-based pedagogical metadata tagging.
# Off by default — adds one LLM call per unique chunk (expensive at scale).
import os as _os
CLAF_TAGGING = _os.getenv("CLAF_TAGGING", "0") == "1"
# Set KG_EXTRACTION=1 in .env to enable LLM-based knowledge graph triple extraction.
# Off by default — adds one LLM call per chunk (expensive at scale).
KG_EXTRACTION = _os.getenv("KG_EXTRACTION", "0") == "1"

# ~500-token target; paragraphs are packed up to this many words, with overlap.
CHUNK_WORDS = 350
OVERLAP_WORDS = 40

# Math-aware chunking constants
MATH_CHUNK_WORDS = 600
MATH_OVERLAP_WORDS = 100

HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
PARA_SPLIT = re.compile(r"\n\s*\n")

# Regex patterns for math boundary detection
MATH_DISPLAY = re.compile(r"\$\$.*?\$\$", re.DOTALL)
MATH_INLINE = re.compile(r"\$[^$\n]+\$")
MATH_ALIGN = re.compile(r"\\begin\{align\*?\}.*?\\end\{align\*?\}", re.DOTALL)
MATH_CASES = re.compile(r"\\begin\{cases\}.*?\\end\{cases\}", re.DOTALL)
MATH_MATRIX = re.compile(
    r"\\begin\{(?:matrix|pmatrix|bmatrix|vmatrix|Vmatrix)\}.*?"
    r"\\end\{(?:matrix|pmatrix|bmatrix|vmatrix|Vmatrix)\}",
    re.DOTALL,
)


def _has_math(text: str) -> bool:
    """Check if text contains significant math content."""
    return bool(
        MATH_DISPLAY.search(text)
        or MATH_ALIGN.search(text)
        or MATH_CASES.search(text)
        or MATH_MATRIX.search(text)
        or MATH_INLINE.search(text)
    )


def _math_regions(text: str) -> list[tuple[int, int]]:
    """Return (start, end) positions of all math regions in text."""
    regions = []
    for pattern in (MATH_DISPLAY, MATH_ALIGN, MATH_CASES, MATH_MATRIX, MATH_INLINE):
        for m in pattern.finditer(text):
            regions.append((m.start(), m.end()))
    regions.sort()
    # Merge overlapping regions
    merged = []
    for start, end in regions:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _split_avoiding_math(body: str, chunk_words: int, overlap_words: int) -> list[str]:
    """Split body into chunks of ~chunk_words, never splitting inside math regions."""
    math_regions = _math_regions(body)
    words = body.split()
    if not words:
        return []

    chunks = []
    i = 0
    step = chunk_words - overlap_words

    while i < len(words):
        end = min(i + chunk_words, len(words))

        # Check if this cut point falls inside a math region
        cut_pos = len(" ".join(words[:end]))
        for m_start, m_end in math_regions:
            if m_start < cut_pos < m_end:
                # Extend to end of math region
                pos = 0
                for j in range(end, len(words)):
                    pos += len(words[j]) + 1
                    if pos >= m_end:
                        end = j + 1
                        break
                break

        chunk = " ".join(words[i:end])
        chunks.append(chunk)

        if end >= len(words):
            break
        i += step

    return chunks


def pack(body: str) -> list[str]:
    """Greedily pack paragraph pieces to ~CHUNK_WORDS, carrying OVERLAP_WORDS."""
    chunks, cur = [], ""
    for piece in _pieces(body):
        candidate = f"{cur} {piece}".strip()
        if cur and len(candidate.split()) > CHUNK_WORDS:
            chunks.append(cur)
            tail = " ".join(cur.split()[-OVERLAP_WORDS:])
            cur = f"{tail} {piece}".strip()
        else:
            cur = candidate
    if cur:
        chunks.append(cur)
    return chunks


def pack_with_math_awareness(body: str, has_math: bool = False) -> list[str]:
    """Pack with math-aware chunk sizes for math-heavy sections.

    For sections with math: larger chunks (600 words) with more overlap (100 words)
    to preserve equation context. Never splits inside math blocks.
    """
    if has_math:
        # Use math-aware splitting for the whole section
        return _split_avoiding_math(body, MATH_CHUNK_WORDS, MATH_OVERLAP_WORDS)
    # Regular paragraph-aware packing
    return pack(body)


def _pieces(body: str) -> list[str]:
    """Paragraphs, with any single over-long paragraph windowed down."""
    out = []
    for para in PARA_SPLIT.split(body):
        para = para.strip()
        if not para:
            continue
        words = para.split()
        if len(words) <= CHUNK_WORDS:
            out.append(para)
            continue
        # Use math-aware splitting for long paragraphs with math
        if _has_math(para):
            out.extend(_split_avoiding_math(para, CHUNK_WORDS, OVERLAP_WORDS))
        else:
            step = CHUNK_WORDS - OVERLAP_WORDS
            for i in range(0, len(words), step):
                out.append(" ".join(words[i : i + CHUNK_WORDS]))
                if i + CHUNK_WORDS >= len(words):
                    break
    return out


def split_by_heading(md: str) -> list[tuple[str, str]]:
    """Return (heading, body) sections. Text before the first heading is kept
    under a synthetic 'Intro' heading."""
    matches = list(HEADING.finditer(md))
    if not matches:
        return [("Intro", md.strip())]

    sections = []
    if matches[0].start() > 0:
        pre = md[: matches[0].start()].strip()
        if pre:
            sections.append(("Intro", pre))

    for i, m in enumerate(matches):
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[start:end].strip()
        if body:
            sections.append((title, body))
    return sections


# ---------------------------------------------------------------------------
# Incremental state helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _load_embed_state() -> dict:
    if EMBED_STATE.exists():
        try:
            return json.loads(EMBED_STATE.read_text())
        except Exception:
            pass
    return {}


def _save_embed_state(state: dict) -> None:
    METADATA.mkdir(parents=True, exist_ok=True)
    EMBED_STATE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Chunk builders
# ---------------------------------------------------------------------------

def build_text_chunks(
    changed_docs: set[str] | None = None,
) -> tuple[list[str], list[dict], list[str]]:
    """Build text chunks for all processed .md files.

    Phase 4 — Semantic chunking:
      If a .docling_doc.pkl sidecar exists (produced by Docling parser), use
      Docling's HybridChunker to produce semantically coherent chunks that
      respect the document hierarchy.  Otherwise, fall back to the original
      heading-split + word-pack approach.
    """
    docs_out, metas, ids = [], [], []
    for md in sorted(PROCESSED.glob("*.md")):
        if changed_docs is not None and md.stem not in changed_docs:
            continue
        subject = _infer_subject(md.stem)
        sidecar = PROCESSED / f"{md.stem}.docling_doc.pkl"

        if sidecar.exists():
            # Semantic chunking via Docling HybridChunker
            try:
                import pickle
                from parsers.docling_parser import semantic_chunks
                docling_doc = pickle.loads(sidecar.read_bytes())
                sem_chunks = semantic_chunks(docling_doc)
                for ci, chunk_data in enumerate(sem_chunks):
                    heading = " > ".join(chunk_data.get("headings", [])) or "Intro"
                    breadcrumb = f"{subject} > " if subject else ""
                    chunk_text = f"{breadcrumb}{heading}\n{chunk_data['text']}"
                    has_math = "$$" in chunk_text or "$" in chunk_text or "\\" in chunk_text
                    has_table = "|" in chunk_text and "---" in chunk_text
                    has_tikz = "vector graphics" in chunk_text or "tikz" in chunk_text.lower()
                    docs_out.append(chunk_text)
                    metas.append({
                        "kind": "text",
                        "doc": md.stem,
                        "doc_type": "pdf",
                        "subject": subject,
                        "chunk_index": ci,
                        "heading": heading,
                        "page_no": chunk_data.get("page_no"),
                        "text": chunk_text,
                        "has_math": has_math,
                        "has_table": has_table,
                        "has_tikz": has_tikz,
                        "chunker": "docling",
                    })
                    ids.append(f"{md.stem}::docling::{ci}")
                continue  # skip word-pack path for this doc
            except Exception as exc:
                print(f"[embed] HybridChunker failed for {md.stem}: {exc}, falling back")

        # Original word-pack chunking (PP-StructureV3 output or fallback).
        # Contextual retrieval: prefix each chunk with its subject + heading so
        # the embedding (and reranker) see the section context, not a bare
        # paragraph that lost its "which chapter am I" signal.
        # Parent-child: embed the small chunk for precise matching; store the
        # full section body as parent_text so the LLM gets richer context.
        text = md.read_text()
        breadcrumb = f"{subject} > " if subject else ""

        # Transcript concept files get doc_type/source_type metadata so slides
        # can distinguish textbook vs. classroom-recorded content.
        is_transcript = md.stem.startswith("concept_")
        extra_meta: dict = (
            {"doc_type": "transcript", "source_type": "classroom"}
            if is_transcript
            else {"doc_type": "pdf"}
        )

        for si, (heading, body) in enumerate(split_by_heading(text)):
            # For transcript files, parent = full file content (400-600 words, fits 3000-char cap)
            if is_transcript:
                parent_text = text[:3000]  # full concept content as parent
            else:
                parent_text = f"{breadcrumb}{heading}\n{body}"[:3000]
            section_has_math = _has_math(body)
            for ci, chunk in enumerate(pack_with_math_awareness(body, section_has_math)):
                chunk_text = f"{breadcrumb}{heading}\n{chunk}"
                has_math = "$$" in chunk_text or "$" in chunk_text or "\\" in chunk_text
                has_table = "|" in chunk_text and "---" in chunk_text
                has_tikz = "vector graphics" in chunk_text or "tikz" in chunk_text.lower()
                docs_out.append(chunk_text)
                metas.append({
                    "kind": "text",
                    "doc": md.stem,
                    "subject": subject,
                    "section_index": si,
                    "chunk_index": ci,
                    "heading": heading,
                    "text": chunk_text,
                    "parent_text": parent_text,
                    "has_math": has_math,
                    "has_table": has_table,
                    "has_tikz": has_tikz,
                    "chunker": "word_pack",
                    **extra_meta,
                })
                ids.append(f"{md.stem}::{si}::{ci}")
    return docs_out, metas, ids


def build_image_chunks(
    changed_docs: set[str] | None = None,
) -> tuple[list[str], list[dict], list[str]]:
    """Build image chunks from labels.json.

    If changed_docs contains the sentinel '__images__', rebuild all image chunks.
    """
    docs_out, metas, ids = [], [], []
    if changed_docs is not None and "__images__" not in changed_docs:
        return docs_out, metas, ids

    for name, lab in load_labels().items():
        kws = ", ".join(lab.get("keywords", []))
        text = f"{lab.get('caption', '')} [{lab.get('type', '')}] {kws}"
        parent_stem = name.split("_p")[0] if "_p" in name else Path(name).stem
        docs_out.append(text)
        metas.append({
            "kind": "image",
            "doc": parent_stem,
            "doc_type": "image",
            "subject": _infer_subject(parent_stem),
            "type": lab.get("type", ""),
            "image": lab.get("image", ""),
            "caption": lab.get("caption", ""),
            "text": text,
        })
        ids.append(f"img::{name}")
    return docs_out, metas, ids


def _infer_subject(stem: str) -> str:
    """Look up subject from the ingest registry (populated by subject_for_file).

    Falls back to filename-prefix heuristic if registry doesn't have the doc.
    """
    if not hasattr(_infer_subject, "_cache"):
        try:
            from ingest import load_registry
            from config import normalize_subject
            reg = load_registry()
            _infer_subject._cache = {}
            for rel, entry in reg.items():
                doc_stem = "".join(
                    c if c.isalnum() or c in "-_" else "_"
                    for c in Path(rel).stem
                )
                subj = entry.get("subject", "")
                if subj:
                    _infer_subject._cache[doc_stem] = subj
        except Exception:
            _infer_subject._cache = {}

    cached = _infer_subject._cache.get(stem)
    if cached:
        return cached

    # Fallback: filename prefix heuristic
    parts = stem.split("_")
    if len(parts) > 1 and parts[0].isalpha() and len(parts[0]) <= 12:
        from config import normalize_subject
        return normalize_subject(parts[0])
    return ""


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run(force_full: bool = False) -> None:
    import store

    state = _load_embed_state()

    if force_full:
        print("[embed] --full rebuild: wiping collection and re-embedding everything...")
        c = store.client()
        store.reset_collection(c)
        changed_docs = None  # all
        images_changed = True
    else:
        # Determine which .md files changed since last embed
        changed_docs: set[str] = set()
        images_changed = False

        for md in sorted(PROCESSED.glob("*.md")):
            current_hash = _sha256_file(md)
            if state.get(f"text::{md.stem}") != current_hash:
                changed_docs.add(md.stem)

        # Check if labels.json changed (proxy for any image label change)
        from config import LABELS
        if LABELS.exists():
            labels_hash = _sha256_file(LABELS)
            if state.get("images::labels") != labels_hash:
                images_changed = True
                changed_docs.add("__images__")

        if not changed_docs:
            print("[embed] nothing changed since last run — index is up to date.")
            return

        print(f"[embed] incremental: {len(changed_docs - {'__images__'})} doc(s) changed"
              + (", images changed" if images_changed else ""))

        c = store.client()
        if not c.collection_exists(COLLECTION):
            # First run: collection doesn't exist yet — create it with the
            # right schema instead of crashing on the delete-stale-chunks step.
            store.reset_collection(c)
        else:
            store.ensure_indexes(c)

        # Delete stale points for changed text docs
        for stem in changed_docs - {"__images__"}:
            store.delete_by_doc(c, stem)
            print(f"[embed]   removed stale chunks for: {stem}")

        # Delete all image points if labels changed
        if images_changed:
            _delete_image_chunks(c)

    # Build only what changed
    docs, metas, ids = build_text_chunks(changed_docs)
    idocs, imetas, iids = build_image_chunks(changed_docs)
    all_docs = docs + idocs
    all_metas = metas + imetas
    all_ids = ids + iids

    # Optional CLAF tagging (gated by CLAF_TAGGING=1 env var)
    if CLAF_TAGGING and metas:
        from claf import tag_chunk
        print(f"[embed] CLAF tagging {len(metas)} text chunks (may be slow)...")
        for i, (doc_text, meta) in enumerate(zip(docs, metas)):
            subject_hint = meta.get("subject", "")
            tags = tag_chunk(doc_text, subject_hint=subject_hint)
            meta.update({
                "subject_domain": tags.get("subject_domain", subject_hint or "other"),
                "grade_level": tags.get("grade_level", "intermediate"),
                "cognitive_demand": tags.get("cognitive_demand", "understand"),
                "primary_language": tags.get("primary_language", "en"),
            })
            if (i + 1) % 50 == 0:
                print(f"[embed]   tagged {i + 1}/{len(metas)} chunks...")

    if not all_docs:
        print("[embed] nothing to embed — run extract/label first")
        return

    import torch
    from embeddings import encode_hybrid
    device = "GPU (RTX 4050)" if torch.cuda.is_available() else "CPU"
    print(f"[embed] encoding {len(all_docs)} chunks with BGE-M3 on {device} (dense+sparse)...")
    dense_vecs, sparse_vecs = encode_hybrid(all_docs)

    store.upsert(c, all_ids, dense_vecs, all_metas, sparse_vectors=sparse_vecs)

    # Optional knowledge graph extraction (gated by KG_EXTRACTION=1 env var)
    if KG_EXTRACTION and all_docs:
        from knowledge_graph import extract_and_add
        print(f"[embed] KG extraction for {len(all_docs)} chunks (may be slow)...")
        for i, doc_text in enumerate(all_docs):
            extract_and_add(doc_text)
            if (i + 1) % 50 == 0:
                print(f"[embed]   KG extracted {i + 1}/{len(all_docs)} chunks...")

    print(
        f"[embed] {len(all_docs)} chunks upserted "
        f"({len(iids)} image, {len(ids)} text) "
        f"-> Qdrant collection '{COLLECTION}'"
    )

    # Update embed state
    for md in sorted(PROCESSED.glob("*.md")):
        if changed_docs is None or md.stem in (changed_docs or set()):
            state[f"text::{md.stem}"] = _sha256_file(md)
    from config import LABELS
    if LABELS.exists():
        state["images::labels"] = _sha256_file(LABELS)
    _save_embed_state(state)


def _delete_image_chunks(c) -> None:
    """Delete all image-kind points from the collection."""
    from qdrant_client import models
    try:
        c.delete(
            COLLECTION,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition("kind", models.MatchValue(value="image"))]
                )
            ),
        )
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="Force full rebuild")
    a = ap.parse_args()
    run(force_full=a.full)