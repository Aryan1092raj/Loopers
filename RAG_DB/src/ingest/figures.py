"""Find vector figures (graphs and diagrams drawn with paths) by their "Figure N.M" caption.

Textbooks draw most graphs as vector paths, not embedded images, so image extraction misses them. The
region of a figure runs from the nearest paragraph above the caption down to the caption, and is
tightened to the drawings and labels inside it.
"""
import re

import fitz  # PyMuPDF

CAPTION = re.compile(r"^\s*Figure\s+\d+(\.\d+)?\b")
MIN_PARAGRAPH_CHARS = 60  # a text block this long and wide ends the figure region
MIN_PARAGRAPH_WIDTH = 0.45  # of the page width
MIN_DRAWINGS = 3  # fewer paths than this is a rule or a box, not a figure
MIN_HEIGHT = 40  # points
LABEL_REACH = 12  # axis labels within this distance of the drawings belong to the figure


def caption_figures(page, covered=()) -> list[fitz.Rect]:
    """Regions (PDF points) of the figures on a page, each including its caption.

    `covered` are rects of raster figures already extracted; a caption right below one is skipped.
    """
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    captions = [fitz.Rect(b[:4]) for b in blocks if CAPTION.match(b[4])]
    if not captions:
        return []
    # a hairline has a zero-width rect, which PyMuPDF treats as empty (it never intersects or joins a union)
    drawings = [
        fitz.Rect(r.x0, r.y0, max(r.x1, r.x0 + 1), max(r.y1, r.y0 + 1))
        for r in (fitz.Rect(d["rect"]) for d in page.get_drawings() if d.get("rect"))
    ]
    out = []
    for cap in captions:
        if any(abs(c.y1 - cap.y0) < 40 and min(c.x1, cap.x1) > max(c.x0, cap.x0) for c in covered):
            continue
        paragraphs = [
            b[3] for b in blocks
            if b[3] <= cap.y0 + 1 and len(b[4].strip()) >= MIN_PARAGRAPH_CHARS
            and b[2] - b[0] > MIN_PARAGRAPH_WIDTH * page.rect.width
        ]
        region = fitz.Rect(page.rect.x0, max(paragraphs, default=page.rect.y0), page.rect.x1, cap.y0)
        inside = [d for d in drawings if d.intersects(region) and d.width < 0.95 * page.rect.width]
        if region.height < MIN_HEIGHT or len(inside) < MIN_DRAWINGS:
            continue
        figure = fitz.Rect(inside[0])
        for d in inside[1:]:
            figure |= d
        for b in blocks:
            label = fitz.Rect(b[:4])
            if region.contains(label) and label.intersects(figure + (-LABEL_REACH, -LABEL_REACH, LABEL_REACH, LABEL_REACH)):
                figure |= label
        out.append((figure | cap) & page.rect)
    return out
