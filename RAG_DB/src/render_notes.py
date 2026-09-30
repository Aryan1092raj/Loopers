"""Notes and assignment spec -> PDF via xelatex.

render(spec, output_path, mode="notes") -> Path
  mode="notes" : single article PDF
  mode="exam"  : student PDF at output_path + teacher PDF at stem_teacher.pdf
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

from config import OUTPUT, PALETTE_DEFS, sanitize_unicode, wrap_inline_math
from render_latex import tex_escape, _resolve_image

# ---------------------------------------------------------------------------
# Preamble helpers
# ---------------------------------------------------------------------------

_BASE_PACKAGES = r"""\documentclass[12pt,a4paper]{article}
\usepackage[margin=2cm]{geometry}
\usepackage{fontspec}
\setmainfont{TeX Gyre Pagella}
\setsansfont{TeX Gyre Heros}
\usepackage[Devanagari]{ucharclasses}
\newfontfamily\devanagarifont{Noto Serif Devanagari}[Script=Devanagari]
\setTransitionsFor{Devanagari}{\begingroup\devanagarifont}{\endgroup}
\usepackage{amsmath,amssymb,amsthm}
\usepackage{mathtools}
\usepackage{bm}
\usepackage{caption}
\usepackage{graphicx}
\usepackage{xcolor}
\usepackage[hidelinks]{hyperref}
\usepackage{enumitem}
\usepackage[skins,breakable]{tcolorbox}
\usepackage{float}        % [H] spec for figures — keeps diagram at the exact prose location
\usepackage{placeins}     % \FloatBarrier — blocks figures from drifting past the next \section
"""

def _preamble(extra: str = "") -> str:
    return _BASE_PACKAGES + PALETTE_DEFS + extra + "\n"


# ---------------------------------------------------------------------------
# Notes builder
# ---------------------------------------------------------------------------

def _build_notes_latex(spec: dict) -> str:
    title = tex_escape(sanitize_unicode(spec.get("title", "Notes")))
    subject = tex_escape(sanitize_unicode(spec.get("subject", "")))

    parts = [
        _preamble(),
        f"\\title{{{title} \\\\ \\large {subject}}}\n",
        "\\date{\\today}\n",
        "\\begin{document}\n",
        "\\maketitle\n\n",
    ]

    tikz_counter = [0]  # mutable for nested func
    mermaid_counter = [0]
    graphviz_counter = [0]
    matplotlib_counter = [0]

    for sec in spec.get("sections", []):
        heading = tex_escape(sanitize_unicode(sec.get("heading", "")))
        if heading:
            parts.append(f"\\section{{{heading}}}\n\n")

        prose = sec.get("prose", "")
        if prose:
            # wrap_inline_math first: turn inline math like "c^T x" into "$c^T x$"
            # so pdflatex's text-mode escape doesn't render the caret as literal "^T".
            parts.append(tex_escape(sanitize_unicode(wrap_inline_math(prose))) + "\n\n")

        for math in sec.get("math_blocks", []):
            parts.append(f"\\[\n{sanitize_unicode(math)}\n\\]\n\n")

        tikz = sec.get("tikz_code", "")
        if tikz:
            png = _tikz_to_png(tikz, tikz_counter)
            if png:
                abs_png = str(png).replace("\\", "/")
                caption = tex_escape(sanitize_unicode(sec.get("caption", "")))
                caption_str = f"  \\caption*{{{caption}}}\n" if caption else ""
                parts.append(
                    f"\\begin{{figure}}[H]\n  \\centering\n"
                    f"  \\includegraphics[width=0.85\\textwidth]{{{abs_png}}}\n"
                    f"{caption_str}"
                    f"\\end{{figure}}\n\n"
                )

        mermaid = sec.get("mermaid_code", "")
        if mermaid:
            png = _mermaid_to_png(mermaid, mermaid_counter)
            if png:
                abs_png = str(png).replace("\\", "/")
                caption = tex_escape(sanitize_unicode(sec.get("caption", "")))
                caption_str = f"  \\caption*{{{caption}}}\n" if caption else ""
                parts.append(
                    f"\\begin{{figure}}[H]\n  \\centering\n"
                    f"  \\includegraphics[width=0.85\\textwidth]{{{abs_png}}}\n"
                    f"{caption_str}"
                    f"\\end{{figure}}\n\n"
                )

        graphviz = sec.get("graphviz_code", "")
        if graphviz:
            png = _graphviz_to_png(graphviz, graphviz_counter)
            if png:
                abs_png = str(png).replace("\\", "/")
                caption = tex_escape(sanitize_unicode(sec.get("caption", "")))
                caption_str = f"  \\caption*{{{caption}}}\n" if caption else ""
                parts.append(
                    f"\\begin{{figure}}[H]\n  \\centering\n"
                    f"  \\includegraphics[width=0.85\\textwidth]{{{abs_png}}}\n"
                    f"{caption_str}"
                    f"\\end{{figure}}\n\n"
                )

        matplotlib = sec.get("matplotlib_code", "")
        if matplotlib:
            png = _matplotlib_to_png(matplotlib, matplotlib_counter)
            if png:
                abs_png = str(png).replace("\\", "/")
                caption = tex_escape(sanitize_unicode(sec.get("caption", "")))
                caption_str = f"  \\caption*{{{caption}}}\n" if caption else ""
                parts.append(
                    f"\\begin{{figure}}[H]\n  \\centering\n"
                    f"  \\includegraphics[width=0.85\\textwidth]{{{abs_png}}}\n"
                    f"{caption_str}"
                    f"\\end{{figure}}\n\n"
                )

        img_path = sec.get("image", "")
        if img_path:
            img = _resolve_image(img_path)
            if img:
                abs_png = str(img.resolve()).replace("\\", "/")
                caption = tex_escape(sanitize_unicode(sec.get("caption", "")))
                caption_str = f"  \\caption*{{{caption}}}\n" if caption else ""
                parts.append(
                    f"\\begin{{figure}}[H]\n  \\centering\n"
                    f"  \\includegraphics[width=0.85\\textwidth,height=0.45\\textheight,keepaspectratio]{{{abs_png}}}\n"
                    f"{caption_str}"
                    f"\\end{{figure}}\n\n"
                )

        # Force any deferred floats from THIS section to ship before the next \section.
        # With [H] floats already pinned, this is a belt-and-suspenders fallback.
        parts.append("\\FloatBarrier\n\n")

        for ex in sec.get("examples", []):
            problem = tex_escape(sanitize_unicode(wrap_inline_math(ex.get("problem", ""))))
            steps = ex.get("solution", [])
            parts.append(
                "\\begin{tcolorbox}[title={Example},colback=lightBg,colframe=brandTeal,"
                "fonttitle=\\bfseries,enhanced,breakable]\n"
                f"\\textbf{{Problem:}} {problem}\n\n"
                "\\textbf{Solution:}\n"
                "\\begin{enumerate}\n"
            )
            for step in steps:
                parts.append(f"  \\item {tex_escape(sanitize_unicode(wrap_inline_math(str(step))))}\n")
            parts.append("\\end{enumerate}\n\\end{tcolorbox}\n\n")

    parts.append("\\end{document}\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Exam builder
# ---------------------------------------------------------------------------

def _build_exam_latex(spec: dict, show_solutions: bool = False) -> str:
    title = tex_escape(sanitize_unicode(spec.get("title", "Assignment")))
    subject = tex_escape(sanitize_unicode(spec.get("subject", "")))
    total = spec.get("total_marks", "")

    sol_flag = "\\solutionstrue" if show_solutions else "\\solutionsfalse"

    parts = [
        _preamble("\\newif\\ifsolutions\n" + sol_flag + "\n"),
        f"\\title{{{title} \\\\ \\large {subject}"
        + (f" \\quad [Total: {total} marks]" if total else "")
        + "}\n",
        "\\date{\\today}\n",
        "\\begin{document}\n",
        "\\maketitle\n\n",
        "\\begin{enumerate}[leftmargin=*]\n\n",
    ]

    # index solutions by question num for fast lookup
    sol_map = {s["num"]: s for s in spec.get("solutions", [])}

    tikz_counter = [0]
    mermaid_counter = [0]
    graphviz_counter = [0]
    matplotlib_counter = [0]

    for q in spec.get("questions", []):
        num = q.get("num", "")
        qtype = q.get("type", "short")
        text = tex_escape(sanitize_unicode(wrap_inline_math(q.get("text", ""))))
        marks = q.get("marks", "")
        marks_str = f"\\hfill\\textbf{{[{marks} mark{'s' if marks != 1 else ''}]}}" if marks else ""

        parts.append(f"\\item {text}{marks_str}\n\n")

        for math in q.get("math_blocks", []):
            parts.append(f"\\[\n{sanitize_unicode(math)}\n\\]\n\n")

        tikz = q.get("tikz_code", "")
        if tikz:
            png = _tikz_to_png(tikz, tikz_counter)
            if png:
                abs_png = str(png).replace("\\", "/")
                caption = tex_escape(sanitize_unicode(q.get("caption", "")))
                caption_str = f"  \\caption*{{{caption}}}\n" if caption else ""
                parts.append(
                    f"\\begin{{figure}}[H]\n  \\centering\n"
                    f"  \\includegraphics[width=0.75\\textwidth]{{{abs_png}}}\n"
                    f"{caption_str}"
                    f"\\end{{figure}}\n\n"
                )

        mermaid = q.get("mermaid_code", "")
        if mermaid:
            png = _mermaid_to_png(mermaid, mermaid_counter)
            if png:
                abs_png = str(png).replace("\\", "/")
                caption = tex_escape(sanitize_unicode(q.get("caption", "")))
                caption_str = f"  \\caption*{{{caption}}}\n" if caption else ""
                parts.append(
                    f"\\begin{{figure}}[H]\n  \\centering\n"
                    f"  \\includegraphics[width=0.75\\textwidth]{{{abs_png}}}\n"
                    f"{caption_str}"
                    f"\\end{{figure}}\n\n"
                )

        graphviz = q.get("graphviz_code", "")
        if graphviz:
            png = _graphviz_to_png(graphviz, graphviz_counter)
            if png:
                abs_png = str(png).replace("\\", "/")
                caption = tex_escape(sanitize_unicode(q.get("caption", "")))
                caption_str = f"  \\caption*{{{caption}}}\n" if caption else ""
                parts.append(
                    f"\\begin{{figure}}[H]\n  \\centering\n"
                    f"  \\includegraphics[width=0.75\\textwidth]{{{abs_png}}}\n"
                    f"{caption_str}"
                    f"\\end{{figure}}\n\n"
                )

          # Matplotlib
        matplotlib = q.get("matplotlib_code", "")
        if matplotlib:
            png = _matplotlib_to_png(matplotlib, matplotlib_counter)
            if png:
                abs_png = str(png).replace("\\", "/")
                caption = tex_escape(sanitize_unicode(q.get("caption", "")))
                caption_str = f"  \\caption*{{{caption}}}\n" if caption else ""
                parts.append(
                    f"\\begin{{figure}}[H]\n  \\centering\n"
                    f"  \\includegraphics[width=0.75\\textwidth]{{{abs_png}}}\n"
                    f"{caption_str}"
                    f"\\end{{figure}}\n\n"
                )

        img_path = q.get("image", "")
        if img_path:
            img = _resolve_image(img_path)
            if img:
                abs_png = str(img.resolve()).replace("\\", "/")
                caption = tex_escape(sanitize_unicode(q.get("caption", "")))
                caption_str = f"  \\caption*{{{caption}}}\n" if caption else ""
                parts.append(
                    f"\\begin{{figure}}[H]\n  \\centering\n"
                    f"  \\includegraphics[width=0.5\\textwidth,height=0.3\\textheight,keepaspectratio]{{{abs_png}}}\n"
                    f"{caption_str}"
                    f"\\end{{figure}}\n\n"
                )

        parts.append("\\FloatBarrier\n\n")

        if qtype == "mcq":
            options = q.get("options", [])
            parts.append("\\begin{enumerate}[label=(\\Alph*)]\n")
            for opt in options:
                parts.append(f"  \\item {tex_escape(sanitize_unicode(wrap_inline_math(opt)))}\n")
            parts.append("\\end{enumerate}\n")
            parts.append("\\vspace{0.5cm}\n\n")
        elif qtype == "short":
            if show_solutions:
                sol = sol_map.get(num, {})
                ans = tex_escape(sanitize_unicode(wrap_inline_math(sol.get("answer", ""))))
                exp = tex_escape(sanitize_unicode(wrap_inline_math(sol.get("explanation", ""))))
                parts.append(
                    "\\ifsolutions\n"
                    f"\\textit{{Answer: {ans}}} {('--- ' + exp) if exp else ''}\n"
                    "\\fi\n\n"
                )
            else:
                parts.append("\\vspace{3cm}\n\n")
        elif qtype == "long":
            if show_solutions:
                sol = sol_map.get(num, {})
                ans = tex_escape(sanitize_unicode(wrap_inline_math(sol.get("answer", ""))))
                exp = tex_escape(sanitize_unicode(wrap_inline_math(sol.get("explanation", ""))))
                parts.append(
                    "\\ifsolutions\n"
                    f"\\textit{{Answer: {ans}}}\\\\\n"
                    f"{exp}\n"
                    "\\fi\n\n"
                )
            else:
                parts.append("\\vspace{6cm}\n\n")

    parts.append("\\end{enumerate}\n\\end{document}\n")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Compile helper
# ---------------------------------------------------------------------------

def _compile(tex_src: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tex_src = sanitize_unicode(tex_src)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        tex_file = tmp / "doc.tex"
        tex_file.write_text(tex_src, encoding="utf-8")

        for _ in range(2):
            res = subprocess.run(
                ["xelatex", "-interaction=nonstopmode", "-output-directory", str(tmp), str(tex_file)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )

        pdf = tmp / "doc.pdf"
        if not pdf.exists():
            print(f"[render_notes] xelatex failed for {output_path.name}")
            print(res.stdout[-2000:])
            return output_path  # caller checks existence

        shutil.copy(str(pdf), str(output_path))

    return output_path


# ---------------------------------------------------------------------------
# TikZ -> PNG helper (wraps viz._run_tikz into a temp dir)
# ---------------------------------------------------------------------------

# ponytail: lazy import so viz.py GPU deps don't load at import time
def _tikz_to_png(code: str, counter: list) -> Path | None:
    try:
        from viz import _run_tikz
    except ImportError:
        return None

    # persist in OUTPUT so includegraphics path survives the temp compile dir
    OUTPUT.mkdir(parents=True, exist_ok=True)

    # Clean up stale files from previous runs once, on the first diagram of
    # this document — never mid-document, or we'd delete earlier diagrams'
    # already-embedded PNGs before the final compile.
    if counter[0] == 0:
        for f in OUTPUT.glob("_tikz_notes_*.png"):
            f.unlink(missing_ok=True)

    counter[0] += 1
    out = OUTPUT / f"_tikz_notes_{counter[0]}.png"
    ok = _run_tikz(code, out)
    return out if ok and out.exists() else None


def _mermaid_to_png(code: str, counter: list) -> Path | None:
    try:
        from viz import _run_mermaid
    except ImportError:
        return None
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if counter[0] == 0:
        for f in OUTPUT.glob("_mermaid_notes_*.png"):
            f.unlink(missing_ok=True)
    counter[0] += 1
    out = OUTPUT / f"_mermaid_notes_{counter[0]}.png"
    ok = _run_mermaid(code, out)
    return out if ok and out.exists() else None


def _graphviz_to_png(code: str, counter: list) -> Path | None:
    try:
        from viz import _run_graphviz
    except ImportError:
        return None
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if counter[0] == 0:
        for f in OUTPUT.glob("_graphviz_notes_*.png"):
            f.unlink(missing_ok=True)
    counter[0] += 1
    out = OUTPUT / f"_graphviz_notes_{counter[0]}.png"
    ok = _run_graphviz(code, out)
    return out if ok and out.exists() else None


def _matplotlib_to_png(code: str, counter: list) -> Path | None:
    try:
        from viz import _run_matplotlib
    except ImportError:
        return None
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if counter[0] == 0:
        for f in OUTPUT.glob("_matplotlib_notes_*.png"):
            f.unlink(missing_ok=True)
    counter[0] += 1
    out = OUTPUT / f"_matplotlib_notes_{counter[0]}.png"
    ok = _run_matplotlib(code, out)
    return out if ok and out.exists() else None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(spec: dict, output_path: Path, mode: str = "notes") -> Path:
    output_path = Path(output_path)

    if mode == "notes":
        src = _build_notes_latex(spec)
        return _compile(src, output_path)

    # mode == "exam"
    student_src = _build_exam_latex(spec, show_solutions=False)
    _compile(student_src, output_path)

    teacher_path = output_path.parent / (output_path.stem + "_teacher.pdf")
    teacher_src = _build_exam_latex(spec, show_solutions=True)
    _compile(teacher_src, teacher_path)

    return output_path
