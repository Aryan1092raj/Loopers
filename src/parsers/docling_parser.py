"""Docling parser wrapper for Humanities/Social Science PDFs (Phase 3).

Docling uses RT-DETR layout detection (>85% mAP) + TableFormer for high-
fidelity table reconstruction with row/column spans.  Outputs a unified
DoclingDocument that HybridChunker can traverse hierarchically (Phase 4).

Install:
    pip install docling

Falls back gracefully if docling is not installed.
"""
from pathlib import Path


def parse(pdf_path: str | Path) -> tuple[str | None, object | None]:
    """Parse a Humanities PDF with Docling.

    Returns:
        (markdown_text, docling_document) — both None on failure or missing dep.
        docling_document can be passed to HybridChunker for semantic chunking.
    """
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return None, None  # Docling not installed — caller falls back to PP-StructureV3

    try:
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        doc = result.document
        md_text = doc.export_to_markdown()
        return md_text, doc
    except Exception as exc:
        print(f"[parsers/docling] Warning: Docling parsing failed for {pdf_path}: {exc}")
        return None, None


def semantic_chunks(
    doc,
    max_tokens: int = 512,
    embed_model: str = "BAAI/bge-m3",
) -> list[dict]:
    """Use Docling's HybridChunker to produce semantically coherent chunks.

    Each returned dict has:
        text      — chunk content
        headings  — list of ancestor heading strings
        page_no   — source page number (if available)

    Falls back to [] on any error.
    """
    try:
        from docling_core.transforms.chunker import HybridChunker
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(embed_model)
        chunker = HybridChunker(tokenizer=tokenizer, max_tokens=max_tokens)
        chunks = []
        for chunk in chunker.chunk(doc):
            text = chunker.serialize(chunk)
            headings = [h.text for h in getattr(chunk.meta, "headings", [])]
            page_no = None
            try:
                if chunk.meta.doc_items:
                    page_no = chunk.meta.doc_items[0].prov[0].page_no
            except Exception:
                pass
            chunks.append({"text": text, "headings": headings, "page_no": page_no})
        return chunks
    except Exception as exc:
        print(f"[parsers/docling] Warning: HybridChunker failed: {exc}")
        return []
