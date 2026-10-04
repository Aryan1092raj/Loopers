"""Render PDF pages to processed/media/pages/<source_id>/<n>.webp (layout fixed by PLAN §4.2).

Page numbers are 1-based and match `page_no` on units. Existing images are kept, so re-runs are cheap.
Backfill without re-extracting: `cd RAG_DB/src && python -m ingest.pages ../raw/<file>.pdf <source_id>`.
"""
import sys
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from config import PROCESSED

PAGES_DIR = PROCESSED / "media" / "pages"
# A2 bbox values are in page-image pixels, so crops must use this same zoom.
ZOOM = 1.5


def render_pages(pdf: Path, source_id: str) -> Path:
    out = PAGES_DIR / source_id
    out.mkdir(parents=True, exist_ok=True)
    with fitz.open(pdf) as doc:
        for n, page in enumerate(doc, start=1):
            dest = out / f"{n}.webp"
            if dest.exists():
                continue
            pix = page.get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM), colorspace=fitz.csRGB, alpha=False)
            Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(dest, "WEBP", quality=80)
    return out


if __name__ == "__main__":
    pdf = Path(sys.argv[1])
    print(render_pages(pdf, sys.argv[2] if len(sys.argv) > 2 else pdf.stem))
