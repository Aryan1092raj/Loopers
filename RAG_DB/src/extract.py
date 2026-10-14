"""Phase 2 — Structured extraction to Markdown (parallel).

For each registered raw file:
  - PDF: take the text layer per page; if a page has no usable text (a scan),
    render it and run Tesseract OCR. Embedded figures are exported to
    processed/images/ and referenced inline.
  - image: copied into processed/images/ with a stub .md that references it.
  - md: passed through unchanged.

Output: processed/<docname>.md plus figures under processed/images/.

PARALLEL EXECUTION:
  PDFs are CPU-bound (PyMuPDF releases the GIL). Uses ProcessPoolExecutor
  so multiple PDFs are extracted on separate cores simultaneously.
  Workers=min(cpu_count, 8) to avoid OOM on large PDFs.
"""
import json
import os
import re
import shutil
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

import fitz  # PyMuPDF

from config import RAW, PROCESSED, IMAGES, METADATA, classify
from ingest import load_registry
from ingest.figures import caption_figures
from ingest.pages import ZOOM as PAGE_ZOOM

# A page whose extracted text is shorter than this is treated as a scan
# and sent to OCR instead.
MIN_TEXT_CHARS = 40
# Resolution for rendering pages / images to OCR.
OCR_ZOOM = 1.5

import multiprocessing
USE_GPU = os.getenv("USE_GPU_OCR", "0") == "1"
MAX_WORKERS = 1 if USE_GPU else min(multiprocessing.cpu_count(), 4)
EXTRACT_MAX_MB = float(os.getenv("EXTRACT_MAX_MB", "40"))  # larger files are left in raw/ untouched and skipped


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


from html.parser import HTMLParser

class TableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.current_row = []
        self.current_cell = []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag in ('td', 'th'):
            self.in_cell = True
            self.current_cell = []
        elif tag == 'tr':
            self.current_row = []

    def handle_endtag(self, tag):
        if tag in ('td', 'th'):
            self.in_cell = False
            self.current_row.append(" ".join(self.current_cell).strip())
        elif tag == 'tr':
            if self.current_row:
                self.rows.append(self.current_row)

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell.append(data.strip())

def html_to_markdown_table(html_str: str) -> str:
    parser = TableHTMLParser()
    parser.feed(html_str)
    if not parser.rows:
        return ""
    max_cols = max(len(row) for row in parser.rows)
    padded_rows = []
    for row in parser.rows:
        padded_row = row + [""] * (max_cols - len(row))
        padded_rows.append(padded_row)
    if not padded_rows:
        return ""
    md = []
    md.append("| " + " | ".join(padded_rows[0]) + " |")
    md.append("| " + " | ".join(["---"] * max_cols) + " |")
    for row in padded_rows[1:]:
        md.append("| " + " | ".join(row) + " |")
    return "\n".join(md)


def _save_crop_img(img_arr, img_dir: Path, filename: str):
    import cv2
    from PIL import Image
    rgb = cv2.cvtColor(img_arr, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    img_dir.mkdir(parents=True, exist_ok=True)
    img.save(img_dir / filename)


def is_inside(boxA: tuple, boxB: tuple) -> bool:
    ax0, ay0, ax1, ay1 = boxA
    bx0, by0, bx1, by1 = boxB
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    iarea = (ix1 - ix0) * (iy1 - iy0)
    aarea = (ax1 - ax0) * (ay1 - ay0)
    return (iarea / aarea) > 0.6


def is_real_table(table) -> bool:
    cells = table.extract()
    if not cells:
        return False
    rows = len(cells)
    cols = len(cells[0])
    if rows < 2 or cols < 2:
        return False
    non_empty_cells = 0
    total_len = 0
    max_cell_len = 0
    for r in cells:
        for c in r:
            if c and c.strip():
                non_empty_cells += 1
                total_len += len(c.strip())
                max_cell_len = max(max_cell_len, len(c.strip()))
    if non_empty_cells == 0:
        return False
    avg_cell_len = total_len / non_empty_cells
    if avg_cell_len > 150 or max_cell_len > 500:
        return False
    if non_empty_cells < 4:
        return False
    return True


def is_solid_color(pix) -> bool:
    samples = pix.samples
    if not samples:
        return True
    n = pix.n
    if len(samples) < n:
        return True
    num_pixels = len(samples) // n
    step = max(1, num_pixels // 500)
    for c in range(n):
        channel_samples = samples[c::n]
        first = channel_samples[0]
        for b in channel_samples[::step]:
            if b != first:
                return False
    return True


def _figure_entry(pno: int, bbox, scale: float) -> dict:
    """Page and bbox of a saved figure in page-image pixels (ingest.pages.ZOOM); `scale` converts the bbox's units."""
    return {"page_no": pno, "bbox": [round(v * scale, 1) for v in bbox]}


def _write_figure_index(stem: str, figures: dict) -> None:
    """metadata/figures/<stem>.json: {figure file: {page_no, bbox}}, merged into labels by label.py."""
    path = METADATA / "figures" / f"{stem}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(figures, indent=1))


def _add_page(parts: list, pno: int, page_parts: list) -> None:
    """Append one page's text behind a `<!-- page N -->` marker; embed.py turns it into page_no."""
    body = "\n\n".join(p for p in page_parts if p)
    if body:
        parts.append(f"<!-- page {pno} -->\n\n{body}")


# A short text block set this much larger than the body font becomes a `##` heading, so embed.py gets real
# sections instead of one "Intro". The body size is measured per document, so no book's fonts are hard-coded.
HEADING_RATIO = 1.25
HEADING_MAX_CHARS = 120


def _box(x0, y0, x1, y1) -> tuple:
    return (round(x0), round(y0), round(x1), round(y1))


def _body_font_size(doc) -> float:
    """The font size that carries the most characters in the document."""
    from collections import Counter
    sizes = Counter()
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                for s in line["spans"]:
                    sizes[round(s["size"], 1)] += len(s["text"].strip())
    return sizes.most_common(1)[0][0] if sizes else 0.0


def _heading_boxes(page, body_size: float) -> set:
    """Boxes of short text blocks whose every span is at least HEADING_RATIO times the body size."""
    out = set()
    if not body_size:
        return out
    for b in page.get_text("dict")["blocks"]:
        spans = [s for line in b.get("lines", []) for s in line["spans"] if s["text"].strip()]
        text = " ".join(s["text"].strip() for s in spans)
        if (spans and len(text) <= HEADING_MAX_CHARS and any(c.isalpha() for c in text)
                and min(s["size"] for s in spans) >= HEADING_RATIO * body_size):
            out.add(_box(*b["bbox"]))
    return out


def extract_pdf_digital(path: Path, stem: str) -> str:
    from collections import Counter
    doc = fitz.open(path)
    
    # 0. Count how many pages each xref appears on
    xref_page_counts = Counter()
    for page in doc:
        xrefs = {img[0] for img in page.get_images()}
        for xref in xrefs:
            xref_page_counts[xref] += 1
            
    body_size = _body_font_size(doc)
    parts = []
    figures = {}
    img_idx = 1

    for pno, page in enumerate(doc, start=1):
        page_parts = []
        page_width = page.rect.width
        page_height = page.rect.height
        page_area = page_width * page_height
        _page_hashes = []  # perceptual hash dedup per page
        
        # 1. Extract tables
        tables = page.find_tables()
        table_elements = []
        for i, table in enumerate(tables.tables):
            if not is_real_table(table):
                continue
            table_elements.append({
                "type": "table",
                "bbox": table.bbox,
                "content": table.to_markdown()
            })
            
        # 2. Extract images
        images = page.get_images()
        image_elements = []
        seen_xrefs = set()
        
        for img in images:
            xref = img[0]
            if xref in seen_xrefs:
                continue
            # Skip repeating elements (appears on >= 3 pages)
            if xref_page_counts[xref] >= 3:
                continue
            rects = page.get_image_rects(xref)
            if not rects:
                continue
            rect = rects[0]
            
            width = rect.width
            height = rect.height
            area = width * height
            
            if area > 0.8 * page_area:
                continue
            if width < 100 or height < 100 or area < 10_000:
                continue
            if rect.x0 < -10 or rect.y0 < -10 or rect.x1 > page_width + 10 or rect.y1 > page_height + 10:
                continue
                
            seen_xrefs.add(xref)
            short_fname = f"p{pno}_figure_{img_idx}.png"
            img_dir = IMAGES / stem
            img_path = img_dir / short_fname
            fname = f"{stem}/{short_fname}"  # relative path for markdown refs

            try:
                pix = fitz.Pixmap(doc, xref)
                # Skip if image is solid color / blank
                if is_solid_color(pix):
                    pix = None
                    continue
                img_dir.mkdir(parents=True, exist_ok=True)
                saved = False
                try:
                    # Strip alpha channel if present before colorspace conversion
                    if pix.alpha:
                        pix = fitz.Pixmap(pix, 0)  # drop alpha
                    # Convert CMYK (or other non-RGB/gray) to RGB for PNG output
                    if pix.n >= 4:
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    pix.save(img_path)
                    saved = True
                except Exception:
                    # Pillow fallback for ICCBased CMYK that PyMuPDF can't convert
                    try:
                        from PIL import Image
                        import io
                        img_data = doc.extract_image(xref)
                        if img_data and img_data.get("image"):
                            pil_img = Image.open(io.BytesIO(img_data["image"]))
                            if pil_img.mode in ("CMYK", "RGBA", "LA", "P"):
                                pil_img = pil_img.convert("RGB")
                            pil_img.save(img_path, "PNG")
                            saved = True
                    except Exception:
                        pass
                pix = None

                if saved:
                    # Entropy check: skip near-blank images (logos, thin lines, etc.)
                    try:
                        from PIL import Image as _PILImage
                        _pil = _PILImage.open(img_path).convert("L")
                        if _pil.entropy() < 2.5:
                            img_path.unlink(missing_ok=True)
                            saved = False
                    except Exception:
                        pass

                if saved:
                    # Perceptual dedup: skip near-identical images on the same page
                    try:
                        import imagehash
                        from PIL import Image as _PILImage
                        _phash = imagehash.phash(_PILImage.open(img_path))
                        for _seen_hash in _page_hashes:
                            if _phash - _seen_hash <= 8:
                                img_path.unlink(missing_ok=True)
                                saved = False
                                break
                        if saved:
                            _page_hashes.append(_phash)
                    except Exception:
                        pass

                if saved:
                    image_elements.append({
                        "type": "image",
                        "bbox": (rect.x0, rect.y0, rect.x1, rect.y1),
                        "content": f"\n![figure]({Path('images') / fname})\n"
                    })
                    img_idx += 1
            except Exception as e:
                print(f"[extract] Warning: failed to extract image xref {xref} on page {pno}: {e}")
                
        # 2a. Vector figures found by their "Figure N.M" caption (graphs are drawn with paths, not embedded images)
        for rect in caption_figures(page, [fitz.Rect(e["bbox"]) for e in image_elements]):
            short_fname = f"p{pno}_figure_{img_idx}.png"
            pix = page.get_pixmap(clip=rect + (-6, -6, 6, 6), dpi=200)
            if is_solid_color(pix):
                continue
            (IMAGES / stem).mkdir(parents=True, exist_ok=True)
            pix.save(IMAGES / stem / short_fname)
            image_elements.append({
                "type": "image",
                "bbox": (rect.x0, rect.y0, rect.x1, rect.y1),
                "content": f"\n![figure]({Path('images') / stem / short_fname})\n",
            })
            img_idx += 1

        # 2b. Vector figure fallback — older PDFs draw figures with paths,
        # not embedded raster images. Detect pages with significant drawing
        # content but no extracted raster images, and render the drawing
        # region as a raster capture.
        if not image_elements:
            drawings = page.get_drawings()
            if len(drawings) >= 8:
                # Find bounding box of all drawing paths
                draw_rects = [fitz.Rect(d["rect"]) for d in drawings if d.get("rect")]
                if draw_rects:
                    union = draw_rects[0]
                    for r in draw_rects[1:]:
                        union |= r
                    fig_area = union.width * union.height
                    # Only capture if the drawing region is meaningful (>5% of page)
                    # and not the entire page (which would be a background)
                    if 0.05 * page_area < fig_area < 0.85 * page_area:
                        clip = union + fitz.Rect(-5, -5, 5, 5)  # small padding
                        pix = page.get_pixmap(clip=clip, dpi=200)
                        if not is_solid_color(pix):
                            short_fname = f"p{pno}_figure_{img_idx}.png"
                            img_dir = IMAGES / stem
                            img_dir.mkdir(parents=True, exist_ok=True)
                            img_path = img_dir / short_fname
                            fname = f"{stem}/{short_fname}"
                            pix.save(img_path)
                            # Entropy check
                            skip = False
                            try:
                                from PIL import Image as _PILImage
                                _pil = _PILImage.open(img_path).convert("L")
                                if _pil.entropy() < 2.5:
                                    img_path.unlink(missing_ok=True)
                                    skip = True
                            except Exception:
                                pass
                            if not skip:
                                image_elements.append({
                                    "type": "image",
                                    "bbox": (union.x0, union.y0, union.x1, union.y1),
                                    "content": f"\n![figure]({Path('images') / fname})\n"
                                })
                                img_idx += 1
                        pix = None

        # 3. Extract text blocks
        raw_blocks = page.get_text("blocks")
        headings = _heading_boxes(page, body_size)
        text_elements = []
        for b in raw_blocks:
            x0, y0, x1, y1, text, block_no, block_type = b
            if block_type != 0:
                continue
            text = text.strip()
            if not text:
                continue
                
            # Filter headers and footers (top 6% or bottom 6%)
            if y0 < 0.06 * page_height or y1 > 0.94 * page_height:
                if len(text) < 30 or text.isdigit():
                    continue
                    
            # Check if inside table
            inside_table = False
            for t in table_elements:
                if is_inside((x0, y0, x1, y1), t["bbox"]):
                    inside_table = True
                    break
            if inside_table:
                continue
                
            text_elements.append({
                "type": "text",
                "bbox": (x0, y0, x1, y1),
                "content": "## " + " ".join(text.split()) if _box(x0, y0, x1, y1) in headings else text,
                "block_no": block_no
            })
            
        for el in image_elements:  # bbox is in PDF points
            saved_as = re.search(r"(p\d+_figure_\d+\.png)", el["content"])
            if saved_as:
                figures[saved_as.group(1)] = _figure_entry(pno, el["bbox"], PAGE_ZOOM)

        # 4. Merge elements
        final_elements = list(text_elements)
        non_text_elements = table_elements + image_elements
        non_text_elements.sort(key=lambda x: (x["bbox"][1], x["bbox"][0]))
        
        for elem in non_text_elements:
            col_blocks = []
            for idx, block in enumerate(final_elements):
                if block["type"] != "text":
                    continue
                bx0, _, bx1, _ = block["bbox"]
                ex0, _, ex1, _ = elem["bbox"]
                if max(bx0, ex0) < min(bx1, ex1):
                    col_blocks.append((idx, block))
                    
            inserted = False
            if col_blocks:
                above_blocks = [(idx, b) for idx, b in col_blocks if b["bbox"][3] <= elem["bbox"][1]]
                if above_blocks:
                    best_idx, best_block = max(above_blocks, key=lambda x: x[1]["bbox"][3])
                    final_elements.insert(best_idx + 1, elem)
                    inserted = True
                else:
                    below_blocks = [(idx, b) for idx, b in col_blocks if b["bbox"][1] >= elem["bbox"][3]]
                    if below_blocks:
                        best_idx, best_block = min(below_blocks, key=lambda x: x[1]["bbox"][1])
                        final_elements.insert(best_idx, elem)
                        inserted = True
                        
            if not inserted:
                elem_cy = (elem["bbox"][1] + elem["bbox"][3]) / 2
                if final_elements:
                    best_idx = 0
                    min_dist = float('inf')
                    for idx, b in enumerate(final_elements):
                        if b["type"] != "text":
                            continue
                        b_cy = (b["bbox"][1] + b["bbox"][3]) / 2
                        dist = abs(elem_cy - b_cy)
                        if dist < min_dist:
                            min_dist = dist
                            best_idx = idx
                    b = final_elements[best_idx]
                    if elem_cy > (b["bbox"][1] + b["bbox"][3]) / 2:
                        final_elements.insert(best_idx + 1, elem)
                    else:
                        final_elements.insert(best_idx, elem)
                else:
                    final_elements.append(elem)
                    
        for el in final_elements:
            page_parts.append(el["content"])
            
        _add_page(parts, pno, page_parts)
        
    doc.close()
    _write_figure_index(stem, figures)
    return "\n\n".join(p for p in parts if p)


def extract_pdf(path: Path, stem: str) -> str:
    """Extract a PDF to Markdown.

    Routing (Phase 3):
      1. Classify PDF as 'stem' (STEM) or 'humanities'.
      2. STEM -> try MinerU; fall back to PP-StructureV3 if not installed/fails.
      3. Humanities -> try Docling (saves sidecar .docling_doc.pkl for HybridChunker).
                       Fall back to PP-StructureV3 if not installed/fails.
      4. PP-StructureV3 is always the final fallback.
    """
    import sys

    # Add src/parsers to path so submodules import cleanly in subprocess workers
    src_dir = str(Path(__file__).parent)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    from parsers.router import classify_pdf
    doc_type = classify_pdf(path)

    if doc_type == "stem":
        # Try MinerU for high-fidelity LaTeX formula recognition
        try:
            from parsers.mineru_parser import parse as mineru_parse
            md = mineru_parse(path, PROCESSED)
            if md and "<!-- page " in md:
                print(f"[extract] {path.name}: MinerU (STEM)")
                return md
            if md:  # no page markers means no locators, which embed rejects
                print("[extract] MinerU output has no page markers, falling back")
        except Exception as exc:
            print(f"[extract] MinerU failed ({exc}), falling back to PP-StructureV3")
    else:
        # Try Docling for high-fidelity table reconstruction
        try:
            from parsers.docling_parser import parse as docling_parse
            md, docling_doc = docling_parse(path)
            if md:
                print(f"[extract] {path.name}: Docling (Humanities)")
                # Save sidecar so embed.py can use HybridChunker
                if docling_doc is not None:
                    import pickle
                    sidecar = PROCESSED / f"{stem}.docling_doc.pkl"
                    sidecar.write_bytes(pickle.dumps(docling_doc))
                return md
        except Exception as exc:
            print(f"[extract] Docling failed ({exc}), falling back to PP-StructureV3")

    # Fallback option based on config
    from config import OCR_ENGINE
    if OCR_ENGINE == "glm-ocr":
        print(f"[extract] {path.name}: GLM-OCR")
        try:
            from parsers.glmocr_parser import parse as glmocr_parse
            md = glmocr_parse(path)
            if md:
                return md
            print(f"[extract] GLM-OCR failed, falling back to PP-StructureV3")
        except Exception as exc:
            print(f"[extract] GLM-OCR failed ({exc}), falling back to PP-StructureV3")

    # Check if PDF is digital or scanned
    doc = fitz.open(path)
    total_chars = sum(len(page.get_text().strip()) for page in doc)
    is_scanned = total_chars < 20 * len(doc)
    doc.close()

    if not is_scanned:
        print(f"[extract] {path.name}: Direct digital parsing")
        return extract_pdf_digital(path, stem)

    # Fallback: original PP-StructureV3 pipeline
    print(f"[extract] {path.name}: PP-StructureV3 (fallback/scanned)")
    return _extract_pdf_ppstructure(path, stem)


def _equation_to_latex(elem: dict) -> str | None:
    """Try to convert an equation crop to LaTeX string.

    Strategy:
      1. If PPStructure returned 'res' with LaTeX, use it directly.
      2. Try PP-FormulaNet (paddleocr formula recognition pipeline).
      3. Fallback: return None (caller will try OCR or skip).
    """
    # Check if PPStructure already extracted LaTeX in its result
    res = elem.get('res', {})
    if isinstance(res, dict):
        latex = res.get('latex', '') or res.get('formula', '')
        if latex:
            return latex.strip('$ ')

    # Try PP-FormulaNet
    img = elem.get('img')
    if img is not None:
        try:
            from ocr import formula_recognize
            latex = formula_recognize(img)
            if latex:
                return latex
        except Exception:
            pass

    return None


def _is_likely_equation(elem: dict) -> bool:
    """Heuristic: detect if a 'figure'-labeled crop is actually an equation/matrix.

    Catches matrices, systems of equations, and inline math that PPStructure
    misclassified as 'figure'. Real figures (diagrams, plots, photos) pass through.

    Checks:
      - Area too small to be a real figure
      - Ink density too sparse (equations/matrices are mostly whitespace)
      - Extreme aspect ratio (wide+short = inline equation)
    """
    import numpy as np
    img = elem.get('img')
    if img is None:
        return False

    h, w = img.shape[:2]
    area = h * w

    # Too tiny to be a real figure — likely an inline equation crop
    if area < 5000:
        return True

    # Compute ink density (ratio of dark pixels)
    gray = np.mean(img, axis=2) if img.ndim == 3 else img.astype(float)
    # Determine if background is light or dark
    mean_val = np.mean(gray)
    if mean_val > 128:
        ink_ratio = np.mean(gray < 128)
    else:
        ink_ratio = np.mean(gray > 128)

    # Equations/matrices: very sparse ink (< 12% fill)
    if ink_ratio < 0.12:
        return True

    # Very wide and short → inline equation
    aspect = w / max(h, 1)
    if aspect > 4.0:
        return True

    return False


def _extract_pdf_ppstructure(path: Path, stem: str) -> str:
    """PP-StructureV3 extraction with smart equation/figure handling.

    Equations → LaTeX text (via PP-FormulaNet or OCR fallback).
    Only genuine figures → PNG images for Moondream captioning.
    """
    import numpy as np
    from ocr import structure_analyze

    doc = fitz.open(path)
    parts = []
    figures = {}
    for pno, page in enumerate(doc, start=1):
        # Render page
        pix = page.get_pixmap(matrix=fitz.Matrix(OCR_ZOOM, OCR_ZOOM))
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            arr = arr[:, :, :3]

        elements = structure_analyze(arr)

        # Sort elements by top-y coordinate to preserve reading order
        sorted_elems = sorted(elements, key=lambda e: (e['bbox'][1], e['bbox'][0]))

        # Check for vector graphics (e.g. TikZ)
        drawings = page.get_drawings()
        has_vector = len(drawings) > 30

        page_parts = []
        if has_vector:
            page_parts.append(f"<!-- page {pno} contains vector graphics: {len(drawings)} paths -->")

        fig_idx = 1
        for elem in sorted_elems:
            etype = elem['type']
            bbox = elem['bbox']

            if etype in ('header', 'footer'):
                continue

            if etype == 'table':
                html = elem['res'].get('html', '')
                md_table = html_to_markdown_table(html)
                if md_table:
                    page_parts.append(md_table)
            elif etype == 'equation':
                # Convert equation to LaTeX text — NOT an image
                latex_text = _equation_to_latex(elem)
                if latex_text:
                    page_parts.append(f"\n$${latex_text}$$\n")
                else:
                    # Fallback: OCR the equation crop as plain text
                    from ocr import ocr_array
                    ocr_text = ocr_array(elem['img']).strip()
                    if ocr_text:
                        page_parts.append(f"\n<!-- equation (OCR) -->\n{ocr_text}\n")
                    # If both fail, skip silently — don't save as image
            elif etype == 'figure':
                # Heuristic: check if this "figure" is actually an equation/matrix
                if _is_likely_equation(elem):
                    latex_text = _equation_to_latex(elem)
                    if latex_text:
                        page_parts.append(f"\n$${latex_text}$$\n")
                        continue
                    from ocr import ocr_array
                    ocr_text = ocr_array(elem['img']).strip()
                    if ocr_text:
                        page_parts.append(f"\n<!-- equation (reclassified) -->\n{ocr_text}\n")
                        continue
                # Genuine figure — save as image for Moondream captioning
                short_fname = f"p{pno}_figure_{fig_idx}.png"
                img_dir = IMAGES / stem
                _save_crop_img(elem['img'], img_dir, short_fname)
                figures[short_fname] = _figure_entry(pno, bbox, PAGE_ZOOM / OCR_ZOOM)  # bbox is in OCR_ZOOM pixels
                rel_path = Path('images') / stem / short_fname
                page_parts.append(f"\n![figure]({rel_path})\n")
                fig_idx += 1
            else:
                bbox_pdf = [coord / OCR_ZOOM for coord in bbox]
                rect = fitz.Rect(bbox_pdf)
                text = page.get_text("text", clip=rect).strip()

                if not text:
                    text = " ".join(line['text'] for line in elem['res']).strip()

                if text:
                    if etype == 'title':
                        page_parts.append(f"## {text}")
                    elif etype == 'list':
                        page_parts.append(f"* {text}")
                    else:
                        page_parts.append(text)

        _add_page(parts, pno, page_parts)

    doc.close()
    _write_figure_index(stem, figures)
    return "\n\n".join(p for p in parts if p)


def extract_image(path: Path, stem: str) -> str:
    img_dir = IMAGES / stem
    img_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{path.stem}{path.suffix.lower()}"
    dest = img_dir / fname
    shutil.copy(path, dest)
    ocr = ""
    try:
        from ocr import ocr_path
        ocr = ocr_path(path).strip()
    except Exception as e:  # noqa: BLE001
        print(f"[extract] OCR on {path.name} failed: {e}")
    body = f"# {path.stem}\n\n![figure]({Path('images') / stem / fname})\n"
    if ocr:
        body += f"\n<!-- OCR text -->\n{ocr}\n"
    return body


def extract_pptx(path: Path, stem: str) -> str:
    from io import BytesIO

    from PIL import Image
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    prs = Presentation(path)
    md_lines = []
    figures = {}  # s<slide>_figure_<n>.png -> {"slide_no": n}; label.py copies it into the figure's unit

    for idx, slide in enumerate(prs.slides, 1):
        md_lines.append(f"\n<!-- slide {idx} -->\n## Slide {idx}")
        
        # Extract title if present
        title = ""
        if slide.shapes.title:
            title = slide.shapes.title.text.strip()
            if title:
                md_lines.append(f"### {title}\n")
                
        for shape in slide.shapes:
            # Skip the title shape as we handled it
            if shape == slide.shapes.title:
                continue
                
            # If shape has text
            if shape.has_text_frame:
                text = []
                for paragraph in shape.text_frame.paragraphs:
                    p_text = "".join(run.text for run in paragraph.runs).strip()
                    if p_text:
                        text.append(p_text)
                if text:
                    md_lines.append("\n".join(text))
            
            # If shape is a table
            elif shape.has_table:
                table_md = []
                table = shape.table
                for row_idx, row in enumerate(table.rows):
                    row_cells = []
                    for cell in row.cells:
                        row_cells.append(cell.text.strip().replace("\n", " "))
                    table_md.append("| " + " | ".join(row_cells) + " |")
                    if row_idx == 0:
                        table_md.append("| " + " | ".join(["---"] * len(row_cells)) + " |")
                if table_md:
                    md_lines.append("\n".join(table_md))

            # Pictures become figure units, captioned by label.py like PDF figures (PS 1d)
            elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                name = f"s{idx}_figure_{len([f for f in figures if f.startswith(f's{idx}_')]) + 1}.png"
                (IMAGES / stem).mkdir(parents=True, exist_ok=True)
                Image.open(BytesIO(shape.image.blob)).save(IMAGES / stem / name)
                figures[name] = {"slide_no": idx}
                md_lines.append(f"![figure](images/{stem}/{name})")

    _write_figure_index(stem, figures)
    return "\n".join(md_lines)


# ---------------------------------------------------------------------------
# Worker function — must be top-level for ProcessPoolExecutor pickling
# ---------------------------------------------------------------------------

def _extract_one(args: tuple) -> tuple[str, str, str]:
    """Process a single file. Returns (rel, status, md_text_or_error)."""
    rel, ftype, raw_str, processed_str, images_str = args
    raw_path = Path(raw_str)
    processed_path = Path(processed_str)
    images_path = Path(images_str)

    src = raw_path / rel
    stem = _slug(Path(rel).stem)

    try:
        if ftype == "pdf":
            md = extract_pdf(src, stem)
            from ingest.pages import render_pages
            from ingest.registry import sha256
            render_pages(src, sha256(src)[:16])  # page images are keyed by source_id, like units
        elif ftype == "image":
            md = extract_image(src, stem)
        elif ftype == "md":
            md = src.read_text()
        elif ftype == "pptx":
            md = extract_pptx(src, stem)
            from ingest.pages import render_slides
            from ingest.registry import sha256
            render_slides(src, sha256(src)[:16])  # slide n is n.webp, which cite.py finds through slide_no
        else:
            return rel, "skipped", f"unsupported type: {ftype}"

        out = processed_path / f"{stem}.md"
        out.write_text(md, encoding="utf-8")
        return rel, "extracted", str(out.relative_to(processed_path.parent))
    except Exception as e:  # noqa: BLE001
        return rel, "failed", str(e)


def run() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    IMAGES.mkdir(parents=True, exist_ok=True)
    reg = load_registry()
    if not reg:
        print("[extract] registry empty — run ingest first")
        return

    # Build work items
    work = []
    skipped_count = 0
    for rel, entry in reg.items():
        if entry.get("status") == "extracted":
            stem = _slug(Path(rel).stem)
            if (PROCESSED / f"{stem}.md").exists():
                skipped_count += 1
                continue
        path = RAW / rel
        # ponytail: size cap, not a per-file timeout; a file this big never finished inside the job's 600 s and failed every later upload too
        if path.is_file() and path.stat().st_size > EXTRACT_MAX_MB * 1024 * 1024:
            print(f"[extract] skip {rel}: {path.stat().st_size / 1048576:.0f} MB is over EXTRACT_MAX_MB={EXTRACT_MAX_MB:g}")
            continue
        ftype = entry.get("type", classify(path))
        work.append((rel, ftype, str(RAW), str(PROCESSED), str(IMAGES)))

    if skipped_count > 0:
        print(f"[extract] skipped {skipped_count} already extracted file(s)")

    if not work:
        print("[extract] nothing to extract — all files up to date")
        return

    print(f"[extract] processing {len(work)} file(s) with up to {MAX_WORKERS} workers...")

    results: dict[str, tuple[str, str]] = {}  # rel -> (status, detail)

    with ProcessPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_extract_one, w): w[0] for w in work}
        for future in as_completed(futures):
            rel, status, detail = future.result()
            results[rel] = (status, detail)
            if status == "extracted":
                print(f"[extract] {rel} -> {detail}")
            elif status == "skipped":
                print(f"[extract] skip {rel}: {detail}")
            else:
                print(f"[extract] FAILED {rel}: {detail}")

    # Update registry in main process (single writer, safe)
    for rel, (status, detail) in results.items():
        if status == "extracted":
            stem = _slug(Path(rel).stem)
            reg[rel]["status"] = "extracted"
            reg[rel]["markdown"] = str(PROCESSED / f"{stem}.md")

    from ingest import save_registry
    save_registry(reg)


if __name__ == "__main__":
    run()
