"""PDF router: classify as 'stem' or 'humanities' to pick the right parser.

Detection heuristics (from scaling paper, Section 2):
  STEM flag if ANY of:
    - Drawing density (vector path count / page count) > DRAWING_DENSITY_THRESHOLD
    - 'Computer Modern' or 'CM' math fonts present (LaTeX-typeset STEM docs)
  Otherwise: 'humanities'

Both thresholds are conservative — false positives (routing humanities to MinerU)
are safe (MinerU still works), but false negatives (routing STEM to Docling) cause
mangled equations.
"""
from pathlib import Path

DRAWING_DENSITY_THRESHOLD = 5.0  # average vector drawings per page
MATH_FONT_PREFIXES = ("cmr", "cmmi", "cmsy", "cmex", "msam", "msbm", "stix", "xits")

# Common math/STEM symbols that indicate mathematical/STEM content
MATH_SYMBOLS = {
    "∫", "∑", "∂", "√", "±", "≤", "≥", "π", "θ", "λ", "α", "β", "γ",
    "∈", "∉", "⊆", "⊂", "∪", "∩", "∅", "∞", "≠", "≈", "≡", "∝", "∇",
    "⊗", "⊕", "×", "÷", "∠", "⊥", "∮", "∬", "∭", "λ", "μ", "σ"
}

STEM_SUBJECTS = {
    "physics", "mathematics", "chemistry", "biology",
    "computer_science", "statistics", "astronomy", "anatomy"
}


def classify_pdf(path: str | Path) -> str:
    """Return 'stem' or 'humanities' for a PDF file.

    STEM is flagged if:
      - Subject derived from folder matches a known STEM subject
      - Math fonts prefix is present
      - Drawing density is above DRAWING_DENSITY_THRESHOLD
      - Text contains any of the known STEM/math symbols
    """
    try:
        import fitz  # PyMuPDF
        from config import subject_for_file

        path = Path(path)
        
        # Heuristic 1: Subject folder hint
        subject = subject_for_file(path)
        if subject in STEM_SUBJECTS:
            return "stem"

        doc = fitz.open(str(path))
        n_pages = max(doc.page_count, 1)

        total_drawings = 0
        math_font_found = False
        math_symbol_found = False

        # Read first few pages for symbol scan and check all fonts
        sample_pages = min(n_pages, 5)
        
        for idx, page in enumerate(doc):
            total_drawings += len(page.get_drawings())
            
            # Font scanning (if not already found)
            if not math_font_found:
                for font in page.get_fonts(full=True):
                    font_name = (font[3] or "").lower()
                    if any(font_name.startswith(p) for p in MATH_FONT_PREFIXES):
                        math_font_found = True
                        break
                        
            # Symbol scanning (on first few sample pages)
            if idx < sample_pages and not math_symbol_found:
                text = page.get_text()
                if any(sym in text for sym in MATH_SYMBOLS):
                    math_symbol_found = True

        density = total_drawings / n_pages
        doc.close()

        if math_font_found or math_symbol_found or density > DRAWING_DENSITY_THRESHOLD:
            return "stem"
        return "humanities"

    except Exception:
        return "humanities"
