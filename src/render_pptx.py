"""Slide-spec JSON -> a real, editable .pptx via python-pptx.

Mirrors render_latex.py's per-slide-type handlers (s_title, s_bullets, ...)
but targets PowerPoint shapes instead of LaTeX source. By the time this runs,
generate.py's viz.process_slides() has already turned "tikz"/"matplotlib"
slides into "image" slides pointing at a rendered PNG, so this module only
needs to handle: title, section, bullets, image, diagram, quote, summary,
theorem, derivation, example, definition.

PowerPoint has no LaTeX math typesetting engine, so $...$ segments are
converted to plain Unicode text (delatex()) rather than dropped or left as
raw backslash commands — good enough to read, not pixel-perfect typesetting.
"""
import re
from pathlib import Path

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

from config import ROOT, PROCESSED, IMAGES, _UNICODE_LATEX

SLIDE_W = Inches(13.333)
SLIDE_H = Inches(7.5)
MARGIN = Inches(0.6)

# Cream theme, matching render_latex.py's THEMES["cream"] so PDF/PPTX exports
# of the same deck feel like the same product.
BG = RGBColor(0xF7, 0xF5, 0xEF)
ACCENT = RGBColor(0xA8, 0x63, 0x0E)
TITLE_COLOR = RGBColor(0x18, 0x22, 0x30)
TEXT_COLOR = RGBColor(0x18, 0x22, 0x30)
CARD = RGBColor(0xFF, 0xFF, 0xFF)
BORDER = RGBColor(0xCD, 0xC6, 0xB6)

FONT = "Calibri"

# ---------------------------------------------------------------------------
# LaTeX -> plain Unicode text (no math typesetting engine in PPTX)
# ---------------------------------------------------------------------------

# Reverse config._UNICODE_LATEX ("→" -> "\ensuremath{\rightarrow}") into
# {"\rightarrow": "→", ...} so LLM-written macros become readable symbols
# instead of literal backslash text once the $ delimiters are stripped.
# Only keep entries whose LaTeX form is an actual "\command" — a few entries
# in that table map to plain punctuation or even "" (zero-width space -> "");
# an empty-string key would make s.replace("", uni) insert `uni` between
# EVERY character in the string (a classic Python str.replace("", x) footgun).
_LATEX_TO_UNICODE: dict[str, str] = {}
for _uni, _latex in _UNICODE_LATEX.items():
    m = re.match(r"\\ensuremath\{(.+)\}$", _latex)
    key = m.group(1) if m else _latex
    if key.startswith("\\"):
        _LATEX_TO_UNICODE.setdefault(key, _uni)

# Accent commands aren't in _UNICODE_LATEX (that table is symbols, not
# decorations) — rendered here as real Unicode combining marks. Correct for
# the overwhelmingly common single-letter case (\vec{F}, \hat{n}, \bar{x});
# a multi-letter argument only visually decorates its last character, which
# is an acceptable approximation for a plain-text (non-typeset) fallback.
_ACCENT_COMBINING = {
    "vec": "⃗", "hat": "̂", "widehat": "̂",
    "bar": "̄", "overline": "̄",
    "dot": "̇", "ddot": "̈",
    "tilde": "̃", "widetilde": "̃",
    "breve": "̆", "check": "̌",
    "underline": "̲",
}

_SUP_MAP = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
# Unicode only defines subscripts for this specific letter set — anything
# else (b,c,d,f,g,q,w,y,z) has no subscript glyph and passes through as-is.
_SUB_MAP = str.maketrans(
    "0123456789+-=()aehijklmnoprstuvx",
    "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ",
)


def _accent_sub(m: re.Match) -> str:
    mark = _ACCENT_COMBINING.get(m.group(1))
    return "".join(ch + mark for ch in m.group(2)) if mark else m.group(0)


def _matrix_to_text(env: str, body: str) -> str:
    rows = [r.strip() for r in re.split(r"\\\\", body) if r.strip()]
    parsed = [[_convert_math_body(c.strip()) for c in row.split("&")] for row in rows]
    if env in ("vmatrix", "Vmatrix"):
        sep = "‖" if env == "Vmatrix" else "|"
        return "\n".join(f"{sep} {' '.join(row)} {sep}" for row in parsed)
    open_b, close_b = ("[", "]") if env in ("bmatrix", "Bmatrix") else ("(", ")")
    return "\n".join(f"{open_b} {' '.join(row)} {close_b}" for row in parsed)


def _convert_math_body(body: str) -> str:
    s = body
    s = re.sub(
        r"\\begin\{(p|b|B|v|V)?matrix\}(.*?)\\end\{(p|b|B|v|V)?matrix\}",
        lambda m: _matrix_to_text((m.group(1) or "") + "matrix", m.group(2)),
        s, flags=re.DOTALL,
    )
    s = re.sub(
        r"\\begin\{(?:align|align\*|gather|gather\*|equation|equation\*|multline)\}(.*?)\\end\{(?:align|align\*|gather|gather\*|equation|equation\*|multline)\}",
        lambda m: re.sub(r"\\\\", "\n", m.group(1)).replace("&", " "),
        s, flags=re.DOTALL,
    )
    s = re.sub(
        r"\\begin\{cases\}(.*?)\\end\{cases\}",
        lambda m: "; ".join(p.replace("&", "→").strip() for p in re.split(r"\\\\", m.group(1)) if p.strip()),
        s, flags=re.DOTALL,
    )
    s = re.sub(r"\\begin\{[^}]+\}|\\end\{[^}]+\}", "", s)
    s = re.sub(r"\\(?:text|mathrm|mathbf|mathit|mathsf|mathtt|operatorname)\{([^{}]*)\}", r"\1", s)
    for _ in range(3):  # a couple passes to catch simple nested fractions
        s = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1/\2)", s)
    s = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", s)
    s = re.sub(
        r"\\(vec|hat|widehat|bar|overline|dot|ddot|tilde|widetilde|breve|check|underline)\{([^{}]*)\}",
        _accent_sub, s,
    )
    for name, uni in sorted(_LATEX_TO_UNICODE.items(), key=lambda kv: -len(kv[0])):
        s = s.replace(name, uni)
    s = re.sub(r"\^\{([^{}]*)\}", lambda m: m.group(1).translate(_SUP_MAP), s)
    s = re.sub(r"\^(\w)", lambda m: m.group(1).translate(_SUP_MAP), s)
    s = re.sub(r"_\{([^{}]*)\}", lambda m: m.group(1).translate(_SUB_MAP), s)
    s = re.sub(r"_(\w)", lambda m: m.group(1).translate(_SUB_MAP), s)
    s = re.sub(r"\\[,;:!]", " ", s)
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"\\([A-Za-z]+)", r"\1", s)  # any leftover \command -> bare word
    return s.replace("\\", "").strip()


def delatex(text: str) -> str:
    """Best-effort LaTeX -> plain Unicode text. $...$/\\(...\\)/\\[...\\]/$$...$$
    segments are converted and un-delimited; everything else passes through."""
    if not text:
        return ""
    text = re.sub(r"\$\$(.+?)\$\$", lambda m: _convert_math_body(m.group(1)), text, flags=re.DOTALL)
    text = re.sub(r"\\\[(.+?)\\\]", lambda m: _convert_math_body(m.group(1)), text, flags=re.DOTALL)
    text = re.sub(r"\\\((.+?)\\\)", lambda m: _convert_math_body(m.group(1)), text, flags=re.DOTALL)
    text = re.sub(r"\$(.+?)\$", lambda m: _convert_math_body(m.group(1)), text, flags=re.DOTALL)
    return text


def _ensure_list(val) -> list:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [val] if val.strip() else []
    return []


# ---------------------------------------------------------------------------
# Markdown → rich runs (handles **bold**, *italic*, ***bold-italic***)
# ---------------------------------------------------------------------------

_MD_PATTERN = re.compile(
    r"(\*\*\*(.+?)\*\*\*"   # ***bold-italic***
    r"|\*\*(.+?)\*\*"        # **bold**
    r"|\*(.+?)\*"            # *italic*
    r"|__(.+?)__"            # __bold__
    r"|_(.+?)_)"             # _italic_
)


def _add_rich_text(paragraph, text: str, size=18, color=TEXT_COLOR, font=FONT, prefix=""):
    """Parse markdown bold/italic in `text` and add multiple runs to paragraph."""
    text = delatex(str(text))
    if prefix:
        run = paragraph.add_run()
        _set_run(run, prefix, size=size, color=color, font=font)

    last_end = 0
    for m in _MD_PATTERN.finditer(text):
        # Plain text before this match
        if m.start() > last_end:
            run = paragraph.add_run()
            _set_run(run, text[last_end:m.start()], size=size, color=color, font=font)

        # Determine style
        if m.group(2):      # ***bold-italic***
            content, bold, italic = m.group(2), True, True
        elif m.group(3):    # **bold**
            content, bold, italic = m.group(3), True, False
        elif m.group(4):    # *italic*
            content, bold, italic = m.group(4), False, True
        elif m.group(5):    # __bold__
            content, bold, italic = m.group(5), True, False
        else:               # _italic_
            content, bold, italic = m.group(6), False, True

        run = paragraph.add_run()
        _set_run(run, content, size=size, bold=bold, italic=italic, color=color, font=font)
        last_end = m.end()

    # Trailing plain text
    if last_end < len(text):
        run = paragraph.add_run()
        _set_run(run, text[last_end:], size=size, color=color, font=font)

    # Edge case: no text at all after prefix
    if not text and not prefix:
        run = paragraph.add_run()
        _set_run(run, "", size=size, color=color, font=font)


def _resolve_image(path: str) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if p.is_absolute() and p.exists():
        return p
    for base in (ROOT, PROCESSED, IMAGES):
        cand = base / path
        if cand.exists() and cand.is_file():
            return cand
    matches = list(IMAGES.rglob(p.name))
    return matches[0] if matches else None


def _add_fit_picture(slide, img_path, left_bound, top_bound, max_w, max_h):
    """Add image to slide, scaled to fit within max_w and max_h while preserving aspect ratio, centered horizontally."""
    from PIL import Image
    try:
        with Image.open(img_path) as pil_img:
            orig_w, orig_h = pil_img.size
        aspect = orig_w / orig_h
        fit_w = max_h * aspect
        fit_h = max_h
        if fit_w > max_w:
            fit_w = max_w
            fit_h = max_w / aspect
        # Center horizontally within the bounding box
        left = left_bound + (max_w - fit_w) / 2
        return slide.shapes.add_picture(str(img_path), left, top_bound, width=fit_w, height=fit_h)
    except Exception as e:
        print(f"[render_pptx] failed to fit picture: {e}")
        return slide.shapes.add_picture(str(img_path), left_bound, top_bound, height=max_h)
# ---------------------------------------------------------------------------
# Shape helpers
# ---------------------------------------------------------------------------

def _blank_slide(prs: Presentation):
    layout = prs.slide_layouts[6]  # "Blank"
    slide = prs.slides.add_slide(layout)
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = BG
    return slide


def _textbox(slide, left, top, width, height):
    box = slide.shapes.add_textbox(left, top, width, height)
    box.text_frame.word_wrap = True
    return box


def _set_run(run, text, size=18, bold=False, italic=False, color=TEXT_COLOR, font=FONT):
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    run.font.name = font


def _title_bar(slide, title: str, y=MARGIN):
    box = _textbox(slide, MARGIN, y, SLIDE_W - 2 * MARGIN, Inches(0.9))
    p = box.text_frame.paragraphs[0]
    run = p.add_run()
    _set_run(run, title, size=28, bold=True, color=TITLE_COLOR)
    return box


def _body_area_top(has_title: bool) -> Emu:
    return MARGIN + Inches(1.1) if has_title else MARGIN


def _bullet_list(slide, items: list[str], top, left=MARGIN, width=None, height=None, size=18):
    width = width or (SLIDE_W - 2 * MARGIN)
    height = height or (SLIDE_H - top - MARGIN)
    box = _textbox(slide, left, top, width, height)
    tf = box.text_frame
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.level = 0
        _add_rich_text(p, str(item), size=size, color=TEXT_COLOR, prefix="•  ")
        p.space_after = Pt(10)
    return box


def _source_note(slide, source: dict | None):
    if not source:
        return
    doc = source.get("doc", "")
    loc = source.get("loc", "")
    if not doc:
        return
    text = f"Source: {doc}" + (f" — {loc}" if loc else "")
    box = _textbox(slide, MARGIN, SLIDE_H - Inches(0.5), SLIDE_W - 2 * MARGIN, Inches(0.35))
    run = box.text_frame.paragraphs[0].add_run()
    _set_run(run, text, size=10, italic=True, color=BORDER)


def _card_box(slide, left, top, width, height, border_color=ACCENT):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARD
    shape.line.color.rgb = border_color
    shape.line.width = Pt(1.5)
    shape.shadow.inherit = False
    return shape


# ---------------------------------------------------------------------------
# Per-slide-type handlers — mirrors render_latex.py's s_* functions
# ---------------------------------------------------------------------------

def s_title(prs: Presentation, spec: dict):
    slide = _blank_slide(prs)
    title = spec.get("title", "Untitled Deck")
    subtitle = spec.get("subtitle", "")

    box = _textbox(slide, MARGIN, Inches(2.6), SLIDE_W - 2 * MARGIN, Inches(1.5))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    _set_run(run, title, size=40, bold=True, color=TITLE_COLOR)

    if subtitle:
        sub_box = _textbox(slide, MARGIN, Inches(4.0), SLIDE_W - 2 * MARGIN, Inches(0.8))
        sp = sub_box.text_frame.paragraphs[0]
        sp.alignment = PP_ALIGN.CENTER
        srun = sp.add_run()
        _set_run(srun, subtitle, size=22, color=ACCENT)


def s_section(prs: Presentation, s: dict):
    slide = _blank_slide(prs)
    title = s.get("title", "")
    box = _textbox(slide, MARGIN, Inches(3.2), SLIDE_W - 2 * MARGIN, Inches(1.2))
    p = box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    _set_run(run, title, size=34, bold=True, color=ACCENT)


def s_bullets(prs: Presentation, s: dict):
    slide = _blank_slide(prs)
    title = s.get("title", "")
    if title:
        _title_bar(slide, title)
    _bullet_list(slide, _ensure_list(s.get("bullets", [])), _body_area_top(bool(title)))
    _source_note(slide, s.get("source"))


def s_image(prs: Presentation, s: dict):
    slide = _blank_slide(prs)
    title = s.get("title", "")
    if title:
        _title_bar(slide, title)
    top = _body_area_top(bool(title))
    img = _resolve_image(s.get("image", ""))
    if img:
        avail_h = SLIDE_H - top - Inches(1.0)
        _add_fit_picture(slide, img, MARGIN, top, SLIDE_W - 2 * MARGIN, avail_h)
    caption = s.get("caption", "")
    if caption:
        cap_box = _textbox(slide, MARGIN, SLIDE_H - Inches(0.85), SLIDE_W - 2 * MARGIN, Inches(0.4))
        run = cap_box.text_frame.paragraphs[0].add_run()
        _set_run(run, delatex(caption), size=13, italic=True, color=TEXT_COLOR)
    _source_note(slide, s.get("source"))


def s_diagram(prs: Presentation, s: dict):
    slide = _blank_slide(prs)
    title = s.get("title", "")
    if title:
        _title_bar(slide, title)
    top = _body_area_top(bool(title))
    img = _resolve_image(s.get("image", ""))
    explains = _ensure_list(s.get("explains", []))
    if img:
        # leave room at the bottom for explains bullets if present
        avail_h = SLIDE_H - top - (Inches(2.2) if explains else Inches(1.0))
        pic = _add_fit_picture(slide, img, MARGIN, top, SLIDE_W - 2 * MARGIN, avail_h)
        top = pic.top + pic.height + Inches(0.2)
    if explains:
        _bullet_list(slide, explains, top, size=15)
    _source_note(slide, s.get("source"))


def s_quote(prs: Presentation, s: dict):
    slide = _blank_slide(prs)
    text = delatex(s.get("text", ""))
    source = s.get("source", "")
    box = _textbox(slide, Inches(1.2), Inches(2.6), SLIDE_W - Inches(2.4), Inches(2.0))
    p = box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    _set_run(run, f"“{text}”", size=26, italic=True, color=TITLE_COLOR)
    if source:
        sbox = _textbox(slide, Inches(1.2), Inches(4.8), SLIDE_W - Inches(2.4), Inches(0.5))
        sp = sbox.text_frame.paragraphs[0]
        sp.alignment = PP_ALIGN.RIGHT
        srun = sp.add_run()
        _set_run(srun, f"— {source}", size=16, color=ACCENT)


def s_summary(prs: Presentation, s: dict):
    slide = _blank_slide(prs)
    title = s.get("title", "Key Takeaways")
    _title_bar(slide, title)
    takeaways = _ensure_list(s.get("takeaways", []))
    box = _textbox(slide, MARGIN, _body_area_top(True), SLIDE_W - 2 * MARGIN, SLIDE_H - _body_area_top(True) - MARGIN)
    tf = box.text_frame
    for i, take in enumerate(takeaways):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        _add_rich_text(p, str(take), size=18, color=TEXT_COLOR, prefix="★  ")
        p.space_after = Pt(10)


def s_theorem(prs: Presentation, s: dict):
    slide = _blank_slide(prs)
    num = s.get("number", "")
    title = f"Theorem{(' ' + str(num)) if num else ''}: {s.get('title', '')}".strip(": ")
    _title_bar(slide, title)
    top = _body_area_top(True)

    statement = s.get("statement", "")
    box_h = Inches(1.6)
    _card_box(slide, MARGIN, top, SLIDE_W - 2 * MARGIN, box_h)
    stmt_box = _textbox(slide, MARGIN + Inches(0.25), top + Inches(0.15), SLIDE_W - 2 * MARGIN - Inches(0.5), box_h - Inches(0.3))
    p = stmt_box.text_frame.paragraphs[0]
    _add_rich_text(p, statement, size=17, color=TEXT_COLOR)
    top += box_h + Inches(0.3)

    proof_steps = _ensure_list(s.get("proof_sketch", []))
    if proof_steps:
        label_box = _textbox(slide, MARGIN, top, Inches(3), Inches(0.4))
        lrun = label_box.text_frame.paragraphs[0].add_run()
        _set_run(lrun, "Proof Sketch:", size=16, bold=True, color=ACCENT)
        top += Inches(0.45)
        _bullet_list(slide, proof_steps, top, size=14)
    _source_note(slide, s.get("source"))


def s_derivation(prs: Presentation, s: dict):
    slide = _blank_slide(prs)
    title = s.get("title", "Derivation")
    _title_bar(slide, title)
    top = _body_area_top(True)

    steps = s.get("steps", [])
    lines = []
    for step in steps:
        label = step.get("label", "")
        math = delatex(str(step.get("math", "")))
        note = step.get("note", "")
        line = f"{label + ':  ' if label else ''}{math}" + (f"   ({note})" if note else "")
        lines.append(line)
    box = _textbox(slide, MARGIN, top, SLIDE_W - 2 * MARGIN, Inches(4.2))
    tf = box.text_frame
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        _set_run(run, line, size=18, color=TEXT_COLOR)
        p.space_after = Pt(8)

    result = s.get("result", "")
    if result:
        rbox = _textbox(slide, MARGIN, SLIDE_H - Inches(1.1), SLIDE_W - 2 * MARGIN, Inches(0.6))
        rp = rbox.text_frame.paragraphs[0]
        rp.alignment = PP_ALIGN.CENTER
        rrun = rp.add_run()
        _set_run(rrun, f"∴  {delatex(result)}", size=22, bold=True, color=ACCENT)
    _source_note(slide, s.get("source"))


def s_example(prs: Presentation, s: dict):
    slide = _blank_slide(prs)
    title = s.get("title", "Worked Example")
    _title_bar(slide, title)
    top = _body_area_top(True)

    problem = s.get("problem", "")
    prob_h = Inches(1.2)
    _card_box(slide, MARGIN, top, SLIDE_W - 2 * MARGIN, prob_h, border_color=ACCENT)
    pbox = _textbox(slide, MARGIN + Inches(0.25), top + Inches(0.1), SLIDE_W - 2 * MARGIN - Inches(0.5), prob_h - Inches(0.2))
    p = pbox.text_frame.paragraphs[0]
    _add_rich_text(p, problem, size=16, color=TEXT_COLOR, prefix="Problem: ")
    top += prob_h + Inches(0.25)

    solution = _ensure_list(s.get("solution", []))
    if solution:
        lbl = _textbox(slide, MARGIN, top, Inches(3), Inches(0.4))
        lrun = lbl.text_frame.paragraphs[0].add_run()
        _set_run(lrun, "Solution:", size=15, bold=True, color=ACCENT)
        top += Inches(0.4)
        _bullet_list(slide, solution, top, size=14)

    answer = s.get("answer", "")
    if answer:
        abox = _textbox(slide, MARGIN, SLIDE_H - Inches(1.0), SLIDE_W - 2 * MARGIN, Inches(0.5))
        ap = abox.text_frame.paragraphs[0]
        _add_rich_text(ap, answer, size=18, color=ACCENT, prefix="∴  ")
    _source_note(slide, s.get("source"))


def s_definition(prs: Presentation, s: dict):
    slide = _blank_slide(prs)
    term = s.get("term", "")
    title = f"Definition: {term}" if term else s.get("title", "Definition")
    _title_bar(slide, title)
    top = _body_area_top(True)

    body = delatex(s.get("body", ""))
    box_h = Inches(2.0)
    _card_box(slide, MARGIN, top, SLIDE_W - 2 * MARGIN, box_h, border_color=TITLE_COLOR)
    bbox = _textbox(slide, MARGIN + Inches(0.25), top + Inches(0.15), SLIDE_W - 2 * MARGIN - Inches(0.5), box_h - Inches(0.3))
    brun = bbox.text_frame.paragraphs[0].add_run()
    _set_run(brun, body, size=16, color=TEXT_COLOR)
    top += box_h + Inches(0.3)

    notation = s.get("notation", "")
    if notation:
        nbox = _textbox(slide, MARGIN, top, SLIDE_W - 2 * MARGIN, Inches(0.6))
        np_ = nbox.text_frame.paragraphs[0]
        np_.alignment = PP_ALIGN.CENTER
        nrun = np_.add_run()
        _set_run(nrun, delatex(notation), size=20, italic=True, color=ACCENT)
    _source_note(slide, s.get("source"))


# ---------------------------------------------------------------------------
# Math equation rendering — LaTeX → PNG via matplotlib mathtext
# ---------------------------------------------------------------------------

_MATH_IDX = 0  # monotonic counter for unique filenames


def _next_math_idx() -> int:
    global _MATH_IDX
    _MATH_IDX += 1
    return _MATH_IDX


def _latex_to_mathtext(latex: str) -> str:
    """Convert common LaTeX math to matplotlib mathtext dialect.

    mathtext supports a subset of TeX: Greek, fractions (via \\frac),
    super/subscripts, \\sqrt, common operators. We strip or translate
    constructs it can't handle so it doesn't choke.
    """
    s = latex.strip()
    # Strip display-mode delimiters
    for start, end in [("$$", "$$"), (r"\[", r"\]"), (r"\(", r"\)"), ("$", "$")]:
        if s.startswith(start) and s.endswith(end):
            s = s[len(start):-len(end)]
            break
    # mathtext needs $...$ wrapping
    s = s.strip()
    # Remove \text{}, \mathrm{} etc — mathtext handles bare text
    s = re.sub(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\{([^{}]*)\}", r"\1", s)
    # \ensuremath{X} → X
    s = re.sub(r"\\ensuremath\{([^{}]*)\}", r"\1", s)
    # \left, \right → plain delimiters (mathtext doesn't need them)
    s = re.sub(r"\\(?:left|right)([.(\[|)\]])", r"\1", s)
    s = s.replace(r"\left", "").replace(r"\right", "")
    # Commands mathtext doesn't support → Unicode/plain equivalents
    _UNSUPPORTED = {
        r"\iff": "⟺", r"\Leftrightarrow": "⟺",
        r"\implies": "⟹", r"\Rightarrow": "⟹",
        r"\Leftarrow": "⟸",
        r"\mapsto": "↦", r"\hookrightarrow": "↪",
        r"\langle": "⟨", r"\rangle": "⟩",
        r"\lceil": "⌈", r"\rceil": "⌉",
        r"\lfloor": "⌊", r"\rfloor": "⌋",
        r"\emptyset": "∅", r"\varnothing": "∅",
        r"\forall": "∀", r"\exists": "∃",
        r"\nexists": "∄", r"\therefore": "∴",
        r"\because": "∵",
        r"\subset": "⊂", r"\supset": "⊃",
        r"\subseteq": "⊆", r"\supseteq": "⊇",
        r"\notin": "∉", r"\ni": "∋",
        r"\cup": "∪", r"\cap": "∩",
        r"\setminus": "∖",
        r"\cdots": "⋯", r"\ldots": "…", r"\vdots": "⋮",
        r"\mathbb{R}": "ℝ", r"\mathbb{Z}": "ℤ",
        r"\mathbb{N}": "ℕ", r"\mathbb{Q}": "ℚ",
        r"\mathbb{C}": "ℂ", r"\mathbb{F}": "𝔽",
    }
    for cmd, repl in _UNSUPPORTED.items():
        s = s.replace(cmd, repl)
    # Spacing commands → space
    s = re.sub(r"\\[,;:!]", " ", s)
    s = re.sub(r"\\(?:quad|qquad)", "  ", s)
    # Strip any remaining unknown \commands that would crash mathtext
    s = re.sub(r"\\(?:ensuremath|displaystyle|textstyle)\b", "", s)
    return s


def render_math_image(latex: str, output_dir: Path) -> Path | None:
    """Render a LaTeX math expression to PNG via matplotlib's mathtext engine.

    Returns the PNG path on success, None on failure. No LaTeX installation
    needed — matplotlib's built-in parser handles common math.
    """
    if not latex or not latex.strip():
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        mathtext = _latex_to_mathtext(latex)
        if not mathtext.strip():
            return None

        fig, ax = plt.subplots(figsize=(10, 1.2))
        ax.axis("off")
        fig.patch.set_alpha(0)
        ax.text(
            0.5, 0.5, f"${mathtext}$",
            fontsize=22, ha="center", va="center",
            transform=ax.transAxes,
            color="#182230",
        )

        idx = _next_math_idx()
        out = output_dir / f"math_{idx:03d}.png"
        output_dir.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out), dpi=200, bbox_inches="tight",
                    transparent=True, pad_inches=0.05)
        plt.close(fig)
        return out if out.exists() else None
    except Exception as e:
        print(f"[render_pptx] math image failed: {e}")
        return None


def _add_math_or_text(slide, latex: str, top, left, width, height,
                      output_dir: Path, size=17, color=TEXT_COLOR):
    """Try to render LaTeX as an image; fall back to delatex() Unicode text."""
    img = render_math_image(latex, output_dir) if latex.strip().startswith("$") or "\\" in latex else None
    if img:
        avail_h = height
        try:
            slide.shapes.add_picture(str(img), left, top, height=avail_h)
            return
        except Exception:
            pass
    # Fallback: Unicode text
    box = _textbox(slide, left, top, width, height)
    run = box.text_frame.paragraphs[0].add_run()
    _set_run(run, delatex(latex), size=size, color=color)


def _add_speaker_notes(slide, text: str):
    """Add speaker notes to a slide."""
    if not text:
        return
    try:
        notes = slide.notes_slide
        tf = notes.notes_text_frame
        tf.text = text.strip()
    except Exception:
        pass


def _slide_to_notes(s: dict) -> str:
    """Extract full text content from a slide spec for speaker notes."""
    parts = []
    t = s.get("type", "")
    if s.get("title"):
        parts.append(s["title"])
    if t == "bullets":
        for b in s.get("bullets", []):
            parts.append(f"• {b}")
    elif t == "definition":
        if s.get("body"):
            parts.append(s["body"])
        if s.get("notation"):
            parts.append(f"Notation: {s['notation']}")
    elif t == "theorem":
        if s.get("statement"):
            parts.append(f"Statement: {s['statement']}")
        for step in s.get("proof_sketch", []):
            parts.append(f"  - {step}")
    elif t == "derivation":
        for step in s.get("steps", []):
            label = step.get("label", "")
            math = step.get("math", "")
            note = step.get("note", "")
            parts.append(f"{label}: {math}" + (f" ({note})" if note else ""))
        if s.get("result"):
            parts.append(f"Result: {s['result']}")
    elif t == "example":
        if s.get("problem"):
            parts.append(f"Problem: {s['problem']}")
        for step in s.get("solution", []):
            parts.append(f"  {step}")
        if s.get("answer"):
            parts.append(f"Answer: {s['answer']}")
    elif t == "summary":
        for take in s.get("takeaways", []):
            parts.append(f"★ {take}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Rich slide handlers — McKinsey aesthetic + full content
# ---------------------------------------------------------------------------

def _rich_title_slide(prs: Presentation, spec: dict) -> None:
    """Dark navy title slide — same as _plan_title_slide but from spec dict."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = TITLE_COLOR

    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), SLIDE_W, Inches(0.18))
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()

    t_box = _textbox(slide, Inches(1.2), Inches(2.5), SLIDE_W - Inches(2.4), Inches(1.8))
    p = t_box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    _set_run(run, delatex(spec.get("title", "Presentation")),
             size=40, bold=True, color=RGBColor(0xF7, 0xF5, 0xEF))

    subtitle = spec.get("subtitle", "")
    if subtitle:
        s_box = _textbox(slide, Inches(1.2), Inches(4.3), SLIDE_W - Inches(2.4), Inches(0.7))
        sp = s_box.text_frame.paragraphs[0]
        sp.alignment = PP_ALIGN.CENTER
        srun = sp.add_run()
        _set_run(srun, delatex(subtitle), size=20, color=ACCENT)


def _rich_title_bar(slide, title: str, slide_number: int = 0):
    """Navy title bar across top with white text — McKinsey style."""
    bar_h = Inches(1.05)
    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), SLIDE_W, bar_h)
    bar.fill.solid()
    bar.fill.fore_color.rgb = TITLE_COLOR
    bar.line.fill.background()

    t_box = _textbox(slide, Inches(0.35), Inches(0.08),
                     SLIDE_W - Inches(0.7), bar_h - Inches(0.16))
    p = t_box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    _set_run(run, delatex(title), size=24, bold=True,
             color=RGBColor(0xFF, 0xFF, 0xFF))

    if slide_number:
        n_box = _textbox(slide, SLIDE_W - Inches(0.75), Inches(0.08),
                         Inches(0.6), Inches(0.45))
        np_ = n_box.text_frame.paragraphs[0]
        np_.alignment = PP_ALIGN.RIGHT
        nrun = np_.add_run()
        _set_run(nrun, str(slide_number), size=11,
                 color=RGBColor(0xB0, 0xB8, 0xC8))

    # Bottom accent rule
    rule = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0.7), SLIDE_H - Inches(0.25),
        SLIDE_W - Inches(1.4), Inches(0.04))
    rule.fill.solid()
    rule.fill.fore_color.rgb = ACCENT
    rule.line.fill.background()

    return bar_h


def _rich_blank(prs: Presentation):
    """White background slide for content."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
    return slide


def rs_section(prs, s, num, out_dir):
    slide = _rich_blank(prs)
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = TITLE_COLOR
    title = s.get("title", "")
    box = _textbox(slide, Inches(1.2), Inches(3.0), SLIDE_W - Inches(2.4), Inches(1.5))
    p = box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    _set_run(run, delatex(title), size=34, bold=True, color=ACCENT)


def rs_bullets(prs, s, num, out_dir):
    slide = _rich_blank(prs)
    title = s.get("title", "")
    bar_h = _rich_title_bar(slide, title, num) if title else Inches(0)
    top = bar_h + Inches(0.35) if title else MARGIN

    bullets = _ensure_list(s.get("bullets", []))
    box = _textbox(slide, Inches(0.7), top, SLIDE_W - Inches(1.1), SLIDE_H - top - Inches(0.5))
    tf = box.text_frame
    for i, bullet in enumerate(bullets):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        _add_rich_text(p, str(bullet), size=20, color=TITLE_COLOR, prefix="▪  ")
        p.space_after = Pt(12)
    _source_note(slide, s.get("source"))
    _add_speaker_notes(slide, _slide_to_notes(s))


def rs_image(prs, s, num, out_dir):
    slide = _rich_blank(prs)
    title = s.get("title", "")
    bar_h = _rich_title_bar(slide, title, num) if title else Inches(0)
    top = bar_h + Inches(0.25) if title else MARGIN

    img = _resolve_image(s.get("image", ""))
    if img:
        avail_h = SLIDE_H - top - Inches(1.2)
        _add_fit_picture(slide, img, Inches(0.7), top, SLIDE_W - Inches(1.4), avail_h)
    else:
        # Fallback: show caption/explains prominently instead of blank
        fallback_lines = _ensure_list(s.get("explains", []))
        cap = s.get("caption", "")
        if cap:
            fallback_lines.insert(0, cap)
        if fallback_lines:
            _bullet_list(slide, fallback_lines, top, left=Inches(0.7), size=18)
        else:
            box = _textbox(slide, Inches(1.5), top + Inches(1.0),
                           SLIDE_W - Inches(3.0), Inches(0.5))
            run = box.text_frame.paragraphs[0].add_run()
            _set_run(run, "[Figure not available — refer to source material]",
                     size=14, italic=True, color=BORDER)
    caption = s.get("caption", "")
    if caption and img:
        cap_box = _textbox(slide, Inches(0.7), SLIDE_H - Inches(0.85),
                           SLIDE_W - Inches(1.4), Inches(0.4))
        run = cap_box.text_frame.paragraphs[0].add_run()
        _set_run(run, delatex(caption), size=13, italic=True, color=TEXT_COLOR)
    _source_note(slide, s.get("source"))
    _add_speaker_notes(slide, _slide_to_notes(s))


def rs_diagram(prs, s, num, out_dir):
    """Same as rs_image but adds explains bullets below."""
    slide = _rich_blank(prs)
    title = s.get("title", "")
    bar_h = _rich_title_bar(slide, title, num) if title else Inches(0)
    top = bar_h + Inches(0.25) if title else MARGIN

    img = _resolve_image(s.get("image", ""))
    explains = _ensure_list(s.get("explains", []))
    if img:
        avail_h = SLIDE_H - top - (Inches(2.2) if explains else Inches(1.2))
        pic = _add_fit_picture(slide, img, Inches(0.7), top, SLIDE_W - Inches(1.4), avail_h)
        top = pic.top + pic.height + Inches(0.2)
    elif explains or s.get("caption"):
        # No image — show caption + explains as main content
        cap = s.get("caption", "")
        if cap and cap not in explains:
            explains = [cap] + explains
    else:
        box = _textbox(slide, Inches(1.5), top + Inches(1.0),
                       SLIDE_W - Inches(3.0), Inches(0.5))
        run = box.text_frame.paragraphs[0].add_run()
        _set_run(run, "[Diagram not available — refer to source material]",
                 size=14, italic=True, color=BORDER)
    if explains:
        _bullet_list(slide, explains, top, left=Inches(0.7), height=SLIDE_H - top - Inches(0.75), size=15)
    _source_note(slide, s.get("source"))
    _add_speaker_notes(slide, _slide_to_notes(s))


def rs_theorem(prs, s, num, out_dir):
    slide = _rich_blank(prs)
    t_num = s.get("number", "")
    title = f"Theorem{(' ' + str(t_num)) if t_num else ''}: {s.get('title', '')}".strip(": ")
    bar_h = _rich_title_bar(slide, title, num)
    top = bar_h + Inches(0.3)

    # Statement in accent-bordered card
    statement = s.get("statement", "")
    box_h = Inches(1.8)
    _card_box(slide, Inches(0.5), top, SLIDE_W - Inches(1.0), box_h)

    # Try math image for statement, fall back to text
    _add_math_or_text(slide, statement, top + Inches(0.15),
                      Inches(0.75), SLIDE_W - Inches(1.5), box_h - Inches(0.3),
                      out_dir, size=17)
    top += box_h + Inches(0.25)

    # Proof sketch
    proof_steps = _ensure_list(s.get("proof_sketch", []))
    if proof_steps:
        lbl = _textbox(slide, Inches(0.7), top, Inches(3), Inches(0.4))
        lrun = lbl.text_frame.paragraphs[0].add_run()
        _set_run(lrun, "Proof Sketch:", size=16, bold=True, color=ACCENT)
        top += Inches(0.4)
        _bullet_list(slide, proof_steps, top, left=Inches(0.7), height=SLIDE_H - top - Inches(0.75), size=14)

    _source_note(slide, s.get("source"))
    _add_speaker_notes(slide, _slide_to_notes(s))


def rs_derivation(prs, s, num, out_dir):
    slide = _rich_blank(prs)
    title = s.get("title", "Derivation")
    bar_h = _rich_title_bar(slide, title, num)
    top = bar_h + Inches(0.3)

    steps = s.get("steps", [])
    step_h = Inches(0.55)
    for step in steps:
        if top > SLIDE_H - Inches(1.5):
            break
        label = step.get("label", "")
        math_raw = str(step.get("math", ""))
        note = step.get("note", "")
        
        # Side-by-side horizontal alignment: label on left, math in middle, note on right
        if label:
            lbl_box = _textbox(slide, Inches(0.7), top, Inches(1.8), Inches(0.35))
            lrun = lbl_box.text_frame.paragraphs[0].add_run()
            _set_run(lrun, f"{label}:", size=14, bold=True, color=ACCENT)
            math_left = Inches(2.6)
            math_w = SLIDE_W - Inches(7.1)
        else:
            math_left = Inches(0.7)
            math_w = SLIDE_W - Inches(5.2)

        _add_math_or_text(slide, math_raw, top,
                          math_left, math_w, Inches(0.45),
                          out_dir, size=17, color=TITLE_COLOR)

        if note:
            n_box = _textbox(slide, SLIDE_W - Inches(4.5), top, Inches(3.8), Inches(0.3))
            nrun = n_box.text_frame.paragraphs[0].add_run()
            _set_run(nrun, f"({note})", size=11, italic=True, color=BORDER)
        top += step_h

    result = s.get("result", "")
    if result:
        _add_math_or_text(slide, f"∴  {result}", max(top + Inches(0.2), SLIDE_H - Inches(1.2)),
                          Inches(0.7), SLIDE_W - Inches(1.4), Inches(0.6),
                          out_dir, size=22, color=ACCENT)

    _source_note(slide, s.get("source"))
    _add_speaker_notes(slide, _slide_to_notes(s))


def rs_example(prs, s, num, out_dir):
    slide = _rich_blank(prs)
    title = s.get("title", "Worked Example")
    bar_h = _rich_title_bar(slide, title, num)
    top = bar_h + Inches(0.3)

    # Problem card
    problem = s.get("problem", "")
    prob_h = Inches(1.3)
    _card_box(slide, Inches(0.5), top, SLIDE_W - Inches(1.0), prob_h, border_color=ACCENT)
    lbl_box = _textbox(slide, Inches(0.75), top + Inches(0.1),
                    Inches(1.5), Inches(0.35))
    lbl_run = lbl_box.text_frame.paragraphs[0].add_run()
    _set_run(lbl_run, "Problem:", size=16, bold=True, color=ACCENT)
    _add_math_or_text(slide, problem, top + Inches(0.4),
                      Inches(0.75), SLIDE_W - Inches(1.5), prob_h - Inches(0.5),
                      out_dir, size=16, color=TITLE_COLOR)
    top += prob_h + Inches(0.2)

    # Solution steps
    solution = _ensure_list(s.get("solution", []))
    answer = s.get("answer", "")
    if solution:
        lbl = _textbox(slide, Inches(0.7), top, Inches(3), Inches(0.4))
        lrun = lbl.text_frame.paragraphs[0].add_run()
        _set_run(lrun, "Solution:", size=15, bold=True, color=ACCENT)
        top += Inches(0.35)
        avail_h = SLIDE_H - top - (Inches(1.2) if answer else Inches(0.6))
        _bullet_list(slide, solution, top, left=Inches(0.7), height=avail_h, size=14)

    # Answer
    if answer:
        abox = _textbox(slide, Inches(0.7), SLIDE_H - Inches(1.1),
                        SLIDE_W - Inches(1.4), Inches(0.5))
        ap = abox.text_frame.paragraphs[0]
        _add_rich_text(ap, answer, size=18, color=ACCENT, prefix="∴  ")

    _source_note(slide, s.get("source"))
    _add_speaker_notes(slide, _slide_to_notes(s))


def rs_definition(prs, s, num, out_dir):
    slide = _rich_blank(prs)
    term = s.get("term", "")
    title = f"Definition: {term}" if term else s.get("title", "Definition")
    bar_h = _rich_title_bar(slide, title, num)
    top = bar_h + Inches(0.3)

    # Body in card
    body = s.get("body", "")
    box_h = Inches(2.2)
    _card_box(slide, Inches(0.5), top, SLIDE_W - Inches(1.0), box_h, border_color=TITLE_COLOR)
    bbox = _textbox(slide, Inches(0.75), top + Inches(0.15),
                    SLIDE_W - Inches(1.5), box_h - Inches(0.3))
    bp = bbox.text_frame.paragraphs[0]
    _add_rich_text(bp, body, size=16, color=TITLE_COLOR)
    top += box_h + Inches(0.25)

    # Notation — try math image
    notation = s.get("notation", "")
    if notation:
        _add_math_or_text(slide, notation, top,
                          Inches(0.7), SLIDE_W - Inches(1.4), Inches(0.7),
                          out_dir, size=20, color=ACCENT)

    _source_note(slide, s.get("source"))
    _add_speaker_notes(slide, _slide_to_notes(s))


def rs_quote(prs, s, num, out_dir):
    slide = _rich_blank(prs)
    text = delatex(s.get("text", ""))
    source = s.get("source", "")
    box = _textbox(slide, Inches(1.2), Inches(2.6), SLIDE_W - Inches(2.4), Inches(2.0))
    p = box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    _set_run(run, f"“{text}”", size=26, italic=True, color=TITLE_COLOR)
    if isinstance(source, str) and source:
        sbox = _textbox(slide, Inches(1.2), Inches(4.8), SLIDE_W - Inches(2.4), Inches(0.5))
        sp = sbox.text_frame.paragraphs[0]
        sp.alignment = PP_ALIGN.RIGHT
        srun = sp.add_run()
        _set_run(srun, f"— {source}", size=16, color=ACCENT)
    _add_speaker_notes(slide, _slide_to_notes(s))


def rs_summary(prs, s, num, out_dir):
    slide = _rich_blank(prs)
    title = s.get("title", "Key Takeaways")
    bar_h = _rich_title_bar(slide, title, num)
    top = bar_h + Inches(0.35)

    takeaways = _ensure_list(s.get("takeaways", []))
    box = _textbox(slide, Inches(0.7), top, SLIDE_W - Inches(1.1),
                   SLIDE_H - top - Inches(0.5))
    tf = box.text_frame
    for i, take in enumerate(takeaways):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        _add_rich_text(p, str(take), size=20, color=TITLE_COLOR, prefix="★  ")
        p.space_after = Pt(12)
    _add_speaker_notes(slide, _slide_to_notes(s))


_RICH_HANDLERS = {
    "section": rs_section,
    "bullets": rs_bullets,
    "image": rs_image,
    "diagram": rs_diagram,
    "quote": rs_quote,
    "summary": rs_summary,
    "theorem": rs_theorem,
    "derivation": rs_derivation,
    "example": rs_example,
    "definition": rs_definition,
}


_HANDLERS = {
    "section": s_section,
    "bullets": s_bullets,
    "image": s_image,
    "diagram": s_diagram,
    "quote": s_quote,
    "summary": s_summary,
    "theorem": s_theorem,
    "derivation": s_derivation,
    "example": s_example,
    "definition": s_definition,
}


def render_rich_pptx(spec: dict, output_path: Path) -> Path:
    """Build a McKinsey-styled PPTX directly from the slide spec.

    Uses the SAME rich spec as the PDF renderer — no separate LLM call.
    All slide types (theorem, derivation, definition, example, diagram, etc.)
    get proper visual treatment with math images, speaker notes, and cards.
    """
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    output_path = Path(output_path)
    out_dir = output_path.parent  # for math image scratch files

    _rich_title_slide(prs, spec)

    for idx, slide_spec in enumerate(spec.get("slides", []), start=1):
        stype = slide_spec.get("type", "bullets")
        handler = _RICH_HANDLERS.get(stype, rs_bullets)
        try:
            handler(prs, slide_spec, idx, out_dir)
        except Exception as e:
            print(f"[render_pptx] rich slide '{stype}' failed ({e}); falling back to bullets")
            try:
                rs_bullets(prs, slide_spec, idx, out_dir)
            except Exception:
                pass

    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    n = len(spec.get("slides", []))
    print(f"[render_pptx] Rich deck saved → {output_path.name}  ({n} slides)")
    return output_path
def render(spec: dict, output_path: Path) -> Path:
    """Build a real, editable .pptx from a slide spec. Mirrors render_latex.render()'s
    signature/contract but writes PowerPoint XML instead of compiling LaTeX."""
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    s_title(prs, spec)  # deck title slide

    for slide_spec in spec.get("slides", []):
        stype = slide_spec.get("type", "bullets")
        handler = _HANDLERS.get(stype)
        if handler is None:
            # tikz/matplotlib should already be promoted to "image" by
            # viz.process_slides() before this runs; anything else unknown
            # degrades to bullets rather than silently dropping content.
            handler = s_bullets
        try:
            handler(prs, slide_spec)
        except Exception as e:
            print(f"[render_pptx] slide type '{stype}' failed ({e}); skipping")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    return output_path


# ---------------------------------------------------------------------------
# Template path (optional — load from templates/pptx_template.pptx if present)
# ---------------------------------------------------------------------------

PPTX_TEMPLATE = ROOT / "templates" / "pptx_template.pptx"


# ---------------------------------------------------------------------------
# McKinsey-style renderer for PresentationPlan (schemas.PresentationPlan)
# ---------------------------------------------------------------------------

def _plan_title_slide(prs: Presentation, title: str, subject: str) -> None:
    """Dark navy title slide with centered title and subject subtitle."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    # Dark background
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = TITLE_COLOR  # #182230 — deep navy

    # Decorative accent bar (top strip)
    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0), Inches(0), SLIDE_W, Inches(0.18)
    )
    bar.fill.solid()
    bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()

    # Title text
    t_box = _textbox(slide, Inches(1.2), Inches(2.5), SLIDE_W - Inches(2.4), Inches(1.8))
    tf = t_box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    run = p.add_run()
    _set_run(run, delatex(title), size=40, bold=True,
             color=RGBColor(0xF7, 0xF5, 0xEF))   # cream on navy

    # Subject subtitle
    if subject:
        s_box = _textbox(slide, Inches(1.2), Inches(4.3), SLIDE_W - Inches(2.4), Inches(0.7))
        sp = s_box.text_frame.paragraphs[0]
        sp.alignment = PP_ALIGN.CENTER
        srun = sp.add_run()
        _set_run(srun, subject.replace("_", " ").title(), size=20,
                 color=ACCENT)


def _plan_content_slide(
    prs: Presentation,
    title: str,
    bullets: list[str],
    speaker_notes: str,
    slide_number: int,
) -> None:
    """Clean white content slide: coloured title bar + brief bullets + speaker notes."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank

    # White background
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)

    # ── Title bar (accent-coloured strip across the top) ──────────────────
    bar_h = Inches(1.05)
    bar = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0), Inches(0), SLIDE_W, bar_h
    )
    bar.fill.solid()
    bar.fill.fore_color.rgb = TITLE_COLOR  # navy
    bar.line.fill.background()

    # Title text (inside the bar)
    t_box = _textbox(slide, Inches(0.35), Inches(0.08), SLIDE_W - Inches(0.7), bar_h - Inches(0.16))
    p = t_box.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    run = p.add_run()
    _set_run(run, delatex(title), size=24, bold=True,
             color=RGBColor(0xFF, 0xFF, 0xFF))

    # Slide number (top-right, subtle)
    n_box = _textbox(
        slide,
        SLIDE_W - Inches(0.75), Inches(0.08),
        Inches(0.6), Inches(0.45)
    )
    np_ = n_box.text_frame.paragraphs[0]
    np_.alignment = PP_ALIGN.RIGHT
    nrun = np_.add_run()
    _set_run(nrun, str(slide_number), size=11,
             color=RGBColor(0xB0, 0xB8, 0xC8))

    # ── Bullet area ────────────────────────────────────────────────────────
    bullet_top = bar_h + Inches(0.35)
    bullet_h   = SLIDE_H - bullet_top - Inches(0.5)

    b_box = _textbox(slide, Inches(0.7), bullet_top, SLIDE_W - Inches(1.1), bullet_h)
    tf = b_box.text_frame
    tf.word_wrap = True

    # Bullet style: large dot + text at a readable size
    for i, bullet in enumerate(bullets[:4]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        _set_run(run, f"▪  {delatex(bullet)}", size=22, color=TITLE_COLOR)
        p.space_after = Pt(14)

    # ── Bottom accent rule ─────────────────────────────────────────────────
    rule = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE,
        Inches(0.7), SLIDE_H - Inches(0.25),
        SLIDE_W - Inches(1.4), Inches(0.04)
    )
    rule.fill.solid()
    rule.fill.fore_color.rgb = ACCENT
    rule.line.fill.background()

    # ── Speaker notes ──────────────────────────────────────────────────────
    if speaker_notes:
        notes = slide.notes_slide
        tf_notes = notes.notes_text_frame
        tf_notes.text = speaker_notes.strip()


def render_from_plan(
    plan,                        # schemas.PresentationPlan instance
    output_path: Path,
    template_path: Path | None = None,
) -> Path:
    """Build a McKinsey-style .pptx from a PresentationPlan.

    Brief bullets on slide canvas; full textbook content in speaker notes.
    Loads template_path (or PPTX_TEMPLATE) if the file exists; otherwise
    starts from a blank widescreen presentation.
    """
    tpl = template_path or PPTX_TEMPLATE

    if tpl and Path(tpl).exists():
        prs = Presentation(str(tpl))
        # Ensure widescreen dimensions match our layout
        prs.slide_width  = SLIDE_W
        prs.slide_height = SLIDE_H
        print(f"[render_pptx] Loaded template: {tpl}")
    else:
        prs = Presentation()
        prs.slide_width  = SLIDE_W
        prs.slide_height = SLIDE_H

    # ── Title slide ────────────────────────────────────────────────────────
    _plan_title_slide(prs, plan.presentation_title, plan.subject)

    # ── Content slides ─────────────────────────────────────────────────────
    for idx, slide in enumerate(plan.slides, start=1):
        _plan_content_slide(
            prs,
            title=slide.title,
            bullets=slide.bullets,
            speaker_notes=slide.speaker_notes,
            slide_number=idx,
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    print(f"[render_pptx] McKinsey deck saved → {output_path.name}  ({len(plan.slides)} slides)")
    return output_path
