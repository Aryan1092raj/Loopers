"""Slide-spec JSON -> real PDF via LaTeX Beamer with premium theme styling.

One handler per slide type. Captures all math segments ($...$) safely and escapes
special LaTeX characters elsewhere. Renders trace attribution footers.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from config import ROOT, PROCESSED, IMAGES, sanitize_unicode

W, H = "210mm", "297mm"  # A4 portrait

# Premium Color Themes
THEME_NAME = os.getenv("SLIDE_THEME", "cream").lower()

THEMES = {
    "cream": {
        "bg": "247, 245, 239",          # #f7f5ef (warm cream paper)
        "accent": "168, 99, 14",        # #a8630e (accent light / rich brown)
        "title": "24, 34, 48",          # #182230 (paper ink)
        "text": "24, 34, 48",           # #182230 (paper ink)
        "card": "247, 245, 239",
        "border": "205, 198, 182",      # #cdc6b6 (line-dashed)
    },
    "dark": {
        "bg": "15, 23, 42",          # #0f172a (slate-900)
        "accent": "99, 102, 241",     # #6366f1 (indigo-500)
        "title": "248, 250, 252",     # #f8fafc (slate-50)
        "text": "148, 163, 184",      # #94a3b8 (slate-400)
        "card": "30, 41, 59",         # #1e293b (slate-800)
        "border": "51, 65, 85",       # #334155 (slate-700)
    },
    "light": {
        "bg": "248, 250, 252",        # #f8fafc (slate-50)
        "accent": "13, 148, 136",     # #0d9488 (teal-600)
        "title": "15, 23, 42",         # #0f172a (slate-900)
        "text": "71, 85, 105",        # #475569 (slate-600)
        "card": "255, 255, 255",       # #ffffff (white)
        "border": "226, 232, 240",     # #e2e8f0 (slate-200)
    }
}


def tex_escape(text: str) -> str:
    """Escape LaTeX special characters outside math mode ($...$, $$...$$, \\(...\\), \\[...\\])."""
    if not isinstance(text, str):
        return ""
    # Split by captured math blocks to preserve math environments verbatim
    # Matches $$...$$, \[...\], $...$, \(...\), and bare \ensuremath{...} /
    # \command{...} macros the LLM sometimes emits without $ delimiters.
    pattern = r'(\$\$.*?\$\$|\\\[.*?\\\]|\$.*?\$|\\\(.*?\\\)|\\ensuremath\{(?:[^{}]|\{[^{}]*\})*\})'
    parts = re.split(pattern, text, flags=re.DOTALL)
    escaped_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # Inside a math block, keep as is
            escaped_parts.append(part)
        else:
            # Normal text, escape special LaTeX characters
            p = part
            # Escape backslash first to avoid escaping escapes
            p = p.replace('\\', '\\textbackslash{}')
            p = p.replace('%', '\\%')
            p = p.replace('&', '\\&')
            p = p.replace('_', '\\_')
            p = p.replace('{', '\\{')
            p = p.replace('}', '\\}')
            p = p.replace('#', '\\#')
            p = p.replace('~', '\\textasciitilde{}')
            p = p.replace('^', '\\textasciicircum{}')
            # pdflatex renders bare '<' as '¡' and '>' as '¿' (Spanish inverted
            # punctuation). Escape them so the prose stays readable; users writing
            # comparisons should put them inside math mode ($<$, $>$) or use \le/\ge.
            p = p.replace('<', '\\textless{}')
            p = p.replace('>', '\\textgreater{}')
            escaped_parts.append(p)
    return "".join(escaped_parts)


def strip_math_delimiters(text: str) -> str:
    """Strip leading/trailing math delimiters from a string."""
    if not isinstance(text, str):
        return ""
    text = text.strip()
    # Handle $$...$$
    if text.startswith("$$") and text.endswith("$$"):
        text = text[2:-2]
    # Handle $...$
    elif text.startswith("$") and text.endswith("$"):
        text = text[1:-1]
    # Handle \(...\)
    elif text.startswith(r"\(") and text.endswith(r"\)"):
        text = text[2:-2]
    # Handle \[...\]
    elif text.startswith(r"\[") and text.endswith(r"\]"):
        text = text[2:-2]
    return text.strip()


def _source_note(src: dict | None) -> str:
    if not src:
        return ""
    doc = tex_escape(src.get("doc", ""))
    loc = tex_escape(src.get("loc", ""))
    return f"Source: {doc} {f'-- {loc}' if loc else ''}".strip()


def _resolve_image(path: str) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if p.is_absolute() and p.exists():
        return p
    for base in [ROOT, PROCESSED, IMAGES]:
        try_p = base / path
        if try_p.exists() and try_p.is_file():
            return try_p
    # Exact filename match in flat IMAGES dir
    try_name = IMAGES / p.name
    if try_name.exists() and try_name.is_file():
        return try_name
    # Recursive search in subfolders (new subfolder structure)
    matches = list(IMAGES.rglob(p.name))
    if matches:
        return matches[0]
    return None


def s_title(spec: dict) -> str:
    title = tex_escape(spec.get("title", "Untitled Document"))
    subtitle = tex_escape(spec.get("subtitle", ""))
    
    latex = [
        "\\begin{center}\n",
        f"  {{\\Huge\\bfseries\\color{{title}} {title} \\par}}\n"
    ]
    if subtitle:
        latex.append(f"  \\vspace{{0.3cm}}\n  {{\\Large\\color{{accent}} {subtitle} \\par}}\n")
    
    latex.extend([
        "  \\vspace{0.5cm}\n",
        "  {\\color{accent}\\hrule height 2pt}\n",
        "  \\vspace{0.8cm}\n",
        "\\end{center}\n\n"
    ])
    return "".join(latex)


def s_section(s: dict) -> str:
    title = tex_escape(s.get("title", ""))
    num = tex_escape(str(s.get("number", "1")))
    return f"\\section*{{{title}}}\n\\label{{sec:{num}}}\n\n"


def _ensure_list(val) -> list:
    """Return val as a list. Handles JSON arrays, Python list reprs, and plain strings."""
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("["):
            try:
                import ast
                return ast.literal_eval(s)
            except Exception:
                pass
        return [s] if s else []
    return []


def s_bullets(s: dict) -> str:
    title = tex_escape(s.get("title", ""))
    source = _source_note(s.get("source"))
    bullets = _ensure_list(s.get("bullets", []))
    
    latex = []
    if title:
        latex.append(f"\\subsection*{{{title}}}\n")
    if source:
        latex.append(f"\\setattribution{{{source}}}\n")
        
    latex.append("\\begin{itemize}\n")
    for b in bullets:
        latex.append(f"  \\item {tex_escape(b)}\n")
    latex.append("\\end{itemize}\n\n")
    return "".join(latex)


def s_image(s: dict) -> str:
    title = tex_escape(s.get("title", ""))
    source = _source_note(s.get("source"))
    img_path = s.get("image", "")
    caption = tex_escape(s.get("caption", ""))
    
    img = _resolve_image(img_path)
    
    latex = []
    if title:
        latex.append(f"\\subsection*{{{title}}}\n")
    if source:
        latex.append(f"\\setattribution{{{source}}}\n")
        
    latex.append("\\begin{figure}[htbp]\n  \\centering\n")
    if img:
        abs_img_path = str(img.resolve()).replace("\\", "/")
        latex.append(f"  \\includegraphics[width=0.85\\textwidth,height=0.45\\textheight,keepaspectratio]{{{abs_img_path}}}\n")
    else:
        filename = tex_escape(Path(img_path).name)
        latex.extend([
            "  \\begin{tikzpicture}\n",
            "    \\draw[draw=border,fill=card,rounded corners] (0,0) rectangle (8,5);\n",
            f"    \\node[color=text,text width=7cm,align=center] at (4,2.5) {{\\small [Image: {filename}]}};\n",
            "  \\end{tikzpicture}\n"
        ])
    if caption:
        latex.append(f"  \\caption*{{{caption}}}\n")
    latex.append("\\end{figure}\n\n")
    return "".join(latex)


def s_diagram(s: dict) -> str:
    title = tex_escape(s.get("title", ""))
    source = _source_note(s.get("source"))
    img_path = s.get("image", "")
    explains = s.get("explains", [])
    
    img = _resolve_image(img_path)
    
    latex = []
    if title:
        latex.append(f"\\subsection*{{{title}}}\n")
    if source:
        latex.append(f"\\setattribution{{{source}}}\n")
        
    latex.append("\\begin{figure}[htbp]\n  \\centering\n")
    if img:
        abs_img_path = str(img.resolve()).replace("\\", "/")
        latex.append(f"  \\includegraphics[width=0.8\\textwidth,height=0.4\\textheight,keepaspectratio]{{{abs_img_path}}}\n")
    else:
        filename = tex_escape(Path(img_path).name)
        latex.extend([
            "  \\begin{tikzpicture}\n",
            "    \\draw[draw=border,fill=card,rounded corners] (0,0) rectangle (8,5);\n",
            f"    \\node[color=text,text width=7cm,align=center] at (4,2.5) {{\\small [Diagram: {filename}]}};\n",
            "  \\end{tikzpicture}\n"
        ])
    latex.append("\\end{figure}\n\n")
    
    if explains:
        latex.append("\\begin{itemize}\n")
        for exp in explains:
            latex.append(f"  \\item {tex_escape(exp)}\n")
        latex.append("\\end{itemize}\n\n")
        
    return "".join(latex)


def s_quote(s: dict) -> str:
    text = tex_escape(s.get("text", ""))
    source = tex_escape(s.get("source", ""))
    
    latex = [
        "\\begin{quote}\n",
        f"  \\itshape\\large\\color{{title}} ``{text}''\n"
    ]
    if source:
        latex.append(f"  \\hfill --- {source}\n")
    latex.append("\\end{quote}\n\n")
    return "".join(latex)


def s_summary(s: dict) -> str:
    title = tex_escape(s.get("title", "Key Takeaways"))
    takeaways = _ensure_list(s.get("takeaways", []))
    
    latex = [
        f"\\subsection*{{{title}}}\n",
        "\\begin{itemize}\n"
    ]
    for take in takeaways:
        latex.append(f"  \\item[\\color{{accent}}\\normalsize$\\star$] {tex_escape(take)}\n")
    latex.extend([
        "\\end{itemize}\n\n"
    ])
    return "".join(latex)


def s_theorem(s: dict) -> str:
    num = tex_escape(str(s.get("number", "")))
    title = tex_escape(s.get("title", "Theorem"))
    statement = tex_escape(s.get("statement", ""))
    proof_steps = _ensure_list(s.get("proof_sketch", []))
    corollary = tex_escape(s.get("corollary", ""))
    source = _source_note(s.get("source"))

    latex = [
        f"\\subsection*{{{title}}}\n",
    ]
    if source:
        latex.append(f"\\setattribution{{{source}}}\n")
        
    latex.extend([
        f"\\begin{{tcolorbox}}[colback=card,colframe=accent,coltitle=white,\n"
        f"    title={{\\bfseries Theorem{(' ' + num) if num else ''}}},fonttitle=\\bfseries,\n"
        f"    enhanced,breakable]\n",
        f"  {statement}\n",
        "\\end{tcolorbox}\n",
    ])
    if proof_steps:
        latex.append("\\textbf{Proof Sketch:}\n")
        latex.append("\\begin{enumerate}\n")
        for step in proof_steps:
            latex.append(f"  \\item {tex_escape(step)}\n")
        latex.append("\\end{enumerate}\n")
    if corollary:
        latex.append(f"\\textit{{Corollary: {corollary}}}\n\n")
    return "".join(latex)


def s_derivation(s: dict) -> str:
    title = tex_escape(s.get("title", "Derivation"))
    steps = _ensure_list(s.get("steps", []))
    result = s.get("result", "")
    source = _source_note(s.get("source"))

    latex = []
    if title:
        latex.append(f"\\subsection*{{{title}}}\n")
    if source:
        latex.append(f"\\setattribution{{{source}}}\n")
        
    latex.append("\\begin{align*}\n")
    for step in steps:
        label = tex_escape(str(step.get("label", "")))
        math = strip_math_delimiters(str(step.get("math", "")))   # raw math
        note = tex_escape(str(step.get("note", "")))
        if label:
            latex.append(f"    \\text{{{label}}} &\\quad {math}")
        else:
            latex.append(f"    &\\quad {math}")
        if note:
            latex.append(f" && \\scriptstyle\\text{{({note})}}")
        latex.append(" \\\\\n")
    latex.append("  \\end{align*}\n")
    if result:
        res_clean = strip_math_delimiters(result)
        latex.append(f"  \\[\\boxed{{{res_clean}}}\\]\n\n")
    return "".join(latex)


def s_example(s: dict) -> str:
    title = tex_escape(s.get("title", "Worked Example"))
    problem = tex_escape(s.get("problem", ""))
    solution_steps = _ensure_list(s.get("solution", []))
    answer = s.get("answer", "")
    source = _source_note(s.get("source"))

    latex = [
        f"\\subsection*{{{title}}}\n",
    ]
    if source:
        latex.append(f"\\setattribution{{{source}}}\n")
        
    latex.extend([
        "\\begin{mdframed}[backgroundcolor=card,linecolor=accent,linewidth=1.5pt,\n"
        "    frametitlefont=\\bfseries\\color{accent},frametitle={Problem}]\n",
        f"  {problem}\n",
        "\\end{mdframed}\n",
        "\\textbf{Solution:}\n",
        "\\begin{enumerate}\n",
    ])
    for step in solution_steps:
        latex.append(f"  \\item {tex_escape(step)}\n")
    latex.append("\\end{enumerate}\n")
    if answer:
        answer_stripped = answer.strip()
        if (answer_stripped.startswith("$$") and answer_stripped.endswith("$$")) or \
           (answer_stripped.startswith(r"\[") and answer_stripped.endswith(r"\]")):
            inner = strip_math_delimiters(answer_stripped)
            latex.append(f"  \\[\\therefore\\; {inner}\\]\n\n")
        elif (answer_stripped.startswith("$") and answer_stripped.endswith("$")) or \
             (answer_stripped.startswith(r"\(") and answer_stripped.endswith(r"\)")):
            inner = strip_math_delimiters(answer_stripped)
            latex.append(f"  \\[\\therefore\\; {inner}\\]\n\n")
        else:
            escaped_ans = tex_escape(answer)
            latex.append(f"\n\\noindent\\textbf{{\\color{{accent}}\\ensuremath{{\\therefore}}\\ }} {escaped_ans}\n\n")
    return "".join(latex)


def s_definition(s: dict) -> str:
    title = tex_escape(s.get("title", "Definition"))
    term = tex_escape(s.get("term", ""))
    body = tex_escape(s.get("body", ""))
    notation = s.get("notation", "")   # raw math
    source = _source_note(s.get("source"))

    latex = [
        f"\\subsection*{{{title}}}\n",
    ]
    if source:
        latex.append(f"\\setattribution{{{source}}}\n")
        
    latex.extend([
        "\\begin{tcolorbox}[colback=card,colframe=title,coltitle=white,\n"
        f"    title={{\\bfseries Definition: {term}}},fonttitle=\\bfseries,\n"
        "    enhanced,breakable]\n",
        f"  {body}\n",
    ])
    if notation:
        notation_stripped = notation.strip()
        if (notation_stripped.startswith("$$") and notation_stripped.endswith("$$")) or \
           (notation_stripped.startswith(r"\[") and notation_stripped.endswith(r"\]")):
            latex.append(f"    {notation_stripped}\n")
        elif (notation_stripped.startswith("$") and notation_stripped.endswith("$")) or \
             (notation_stripped.startswith(r"\(") and notation_stripped.endswith(r"\)")):
            inner = strip_math_delimiters(notation_stripped)
            latex.append(f"    \\[ {inner} \\]\n")
        elif "\\" in notation:
            latex.append(f"    \\[ {notation_stripped} \\]\n")
        else:
            latex.append(f"    \\textit{{Notation: {tex_escape(notation)}}}\n")
    latex.extend([
        "  \\end{tcolorbox}\n\n",
    ])
    return "".join(latex)


def s_tikz(s: dict) -> str:
    """Embed LLM-generated TikZ code directly as centered vector graphic.
    Falls back to includegraphics if code is absent but an image path is present.
    Validates TikZ compiles before embedding."""
    title = tex_escape(s.get("title", ""))
    caption = tex_escape(s.get("caption", ""))
    code = s.get("code", "").strip()
    img_path = s.get("image", "")

    latex = []
    if title:
        latex.append(f"\\subsection*{{{title}}}\n")
    latex.append("\\begin{figure}[htbp]\n  \\centering\n")

    if code:
        code_clean = re.sub(r'\\begin\{tikzpicture\}(?:\[.*?\])?', '', code)
        code_clean = re.sub(r'\\end\{tikzpicture\}', '', code_clean)
        code_clean = code_clean.strip()
        
        # Validate TikZ compiles before embedding
        if not _validate_tikz_code(code_clean):
            print(f"[render_latex] TikZ validation failed for '{title}', falling back to placeholder")
            code_clean = "\\node[text] {{\\small [Diagram failed to compile]}};"
        
        latex.append("  \\begin{tikzpicture}[scale=0.95, every node/.style={transform shape}]\n")
        latex.append(f"{code_clean}\n")
        latex.append("  \\end{tikzpicture}\n")
    else:
        img = _resolve_image(img_path)
        if img:
            abs_path = str(img.resolve()).replace("\\", "/")
            latex.append(f"  \\includegraphics[width=0.85\\textwidth,height=0.5\\textheight,keepaspectratio]{{{abs_path}}}\n")

    if caption:
        latex.append(f"  \\caption*{{{caption}}}\n")
    latex.append("\\end{figure}\n\n")
    return "".join(latex)


def _validate_tikz_code(code: str) -> bool:
    """Compile-check TikZ code with pdflatex. Returns True if valid."""
    import tempfile, subprocess
    from config import ROOT
    
    test_tex = f"""\\documentclass[11pt]{{article}}
\\usepackage{tikz}
\\usetikzlibrary{{shapes.geometric,arrows.meta,positioning,calc,backgrounds,shadows,decorations.pathmorphing,patterns,fadings}}
\\definecolor{{brandDark}}{{HTML}}{{1A252C}}
\\definecolor{{brandTeal}}{{HTML}}{{006D77}}
\\definecolor{{brandMint}}{{HTML}}{{83C5BE}}
\\definecolor{{waterBlue}}{{HTML}}{{00B4D8}}
\\definecolor{{waterDeep}}{{HTML}}{{0077B6}}
\\definecolor{{earthBrown}}{{HTML}}{{DDA15E}}
\\definecolor{{earthDark}}{{HTML}}{{BC6C25}}
\\definecolor{{accentOrange}}{{HTML}}{{E76F51}}
\\definecolor{{lightBg}}{{HTML}}{{F8F9FA}}
\\definecolor{{forestGreen}}{{HTML}}{{2A9D8F}}
\\begin{{document}}
\\begin{{tikzpicture}}[scale=0.95, every node/.style={{transform shape}}]
{code}
\\end{{tikzpicture}}
\\end{{document}}"""
    
    with tempfile.TemporaryDirectory(dir=str(ROOT)) as tmpdir:
        tmp_path = Path(tmpdir)
        tex_file = tmp_path / "tikz_test.tex"
        tex_file.write_text(test_tex, encoding="utf-8")
        res = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-output-directory", str(tmp_path), str(tex_file)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT)
        )
    return res.returncode == 0


def s_matplotlib(s: dict) -> str:
    """Render Matplotlib generated plot image."""
    title = tex_escape(s.get("title", ""))
    img_path = s.get("image", "")
    caption = tex_escape(s.get("caption", ""))
    
    img = _resolve_image(img_path)
    
    latex = []
    if title:
        latex.append(f"\\subsection*{{{title}}}\n")
    latex.append("\\begin{figure}[htbp]\n  \\centering\n")
    if img:
        abs_img_path = str(img.resolve()).replace("\\", "/")
        latex.append(f"  \\includegraphics[width=0.85\\textwidth,height=0.45\\textheight,keepaspectratio]{{{abs_img_path}}}\n")
    if caption:
        latex.append(f"  \\caption*{{{caption}}}\n")
    latex.append("\\end{figure}\n\n")
    return "".join(latex)


HANDLERS = {
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
    "tikz": s_tikz,
    "matplotlib": s_matplotlib,
}


def render(spec: dict, out_path: Path) -> Path:
    theme = THEMES.get(THEME_NAME, THEMES["cream"])
    
    latex_src = [
        "\\documentclass[11pt]{article}\n",
        "\\usepackage[margin=0.55in]{geometry}\n",
        "\\usepackage{fontspec}\n",
        "\\setmainfont{TeX Gyre Pagella}\n",
        "\\setsansfont{TeX Gyre Heros}\n",
        "\\usepackage[Devanagari]{ucharclasses}\n",
        "\\newfontfamily\\devanagarifont{Noto Serif Devanagari}[Script=Devanagari]\n",
        "\\setTransitionsFor{Devanagari}{\\begingroup\\devanagarifont}{\\endgroup}\n",
        "\\usepackage{graphicx}\n",
        "\\usepackage{xcolor}\n",
        "\\usepackage{amsmath}\n",
        "\\usepackage{amssymb}\n",
        "\\usepackage{tikz}\n",
        "\\usetikzlibrary{shapes.geometric,arrows.meta,positioning,calc,backgrounds,shadows,decorations.pathmorphing,patterns,fadings}\n",
        "\\usepackage{tcolorbox}\n",
        "\\tcbuselibrary{theorems,skins,breakable}\n",
        "\\usepackage{mdframed}\n",
        "\\usepackage{enumitem}\n",
        "\\usepackage{fancyhdr}\n\n",
        f"\\definecolor{{bg}}{{RGB}}{{{theme['bg']}}}\n",
        f"\\definecolor{{accent}}{{RGB}}{{{theme['accent']}}}\n",
        f"\\definecolor{{title}}{{RGB}}{{{theme['title']}}}\n",
        f"\\definecolor{{text}}{{RGB}}{{{theme['text']}}}\n",
        f"\\definecolor{{card}}{{RGB}}{{{theme['card']}}}\n",
        f"\\definecolor{{border}}{{RGB}}{{{theme['border']}}}\n",
        # Brand palette for TikZ diagrams (from user spec)
        "\\definecolor{brandDark}{HTML}{1A252C}\n",
        "\\definecolor{brandTeal}{HTML}{006D77}\n",
        "\\definecolor{brandMint}{HTML}{83C5BE}\n",
        "\\definecolor{waterBlue}{HTML}{00B4D8}\n",
        "\\definecolor{waterDeep}{HTML}{0077B6}\n",
        "\\definecolor{earthBrown}{HTML}{DDA15E}\n",
        "\\definecolor{earthDark}{HTML}{BC6C25}\n",
        "\\definecolor{accentOrange}{HTML}{E76F51}\n",
        "\\definecolor{lightBg}{HTML}{F8F9FA}\n",
        "\\definecolor{forestGreen}{HTML}{2A9D8F}\n\n",
        "\\pagecolor{bg}\n",
        "\\color{text}\n\n",
        # Custom footline showing page numbers and RAG source
        "\\newcommand{\\insertattribution}{}\n",
        "\\newcommand{\\setattribution}[1]{\\renewcommand{\\insertattribution}{#1}}\n",
        "\\newcommand{\\clearattribution}{\\renewcommand{\\insertattribution}{}}\n\n",
        "\\pagestyle{fancy}\n",
        "\\fancyhf{}\n",
        "\\fancyfoot[C]{\\thepage}\n",
        "\\fancyfoot[R]{\\tiny\\insertattribution}\n",
        "\\renewcommand{\\headrulewidth}{0pt}\n\n",
        "\\begin{document}\n\n"
    ]
    
    # Title
    latex_src.append(s_title(spec))
    
    # Render all slides sequentially
    for s in spec.get("slides", []):
        stype = s.get("type")
        handler = HANDLERS.get(stype)
        if handler:
            # Clear attribution before rendering next segment
            latex_src.append("\\clearattribution\n")
            latex_src.append(handler(s))
        else:
            print(f"[render_latex] Unknown slide type: {stype}")
            
    latex_src.append("\\end{document}\n")
    
    # Compile LaTeX using xelatex in a temporary directory.
    # Sanitize the whole document once: catches raw Unicode in LLM math fields
    # (derivation/notation) that bypass tex_escape, so xelatex won't choke.
    tex_content = sanitize_unicode("".join(latex_src))

    # Clean up nested math: LLMs emit $...$ inside \[...\] which breaks xelatex.
    # Strip top-level $ (outside {}) inside display math and align environments.
    def _strip_top_level_dollar(inner: str) -> str:
        depth = 0
        out = []
        for c in inner:
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
            elif c == '$' and depth == 0:
                continue
            out.append(c)
        return ''.join(out)

    tex_content = re.sub(
        r'\\\[(.*?)\\\]',
        lambda m: '\\[' + _strip_top_level_dollar(m.group(1)) + '\\]',
        tex_content,
        flags=re.DOTALL,
    )
    tex_content = re.sub(
        r'\\begin\{align\*\}(.*?)\\end\{align\*\}',
        lambda m: '\\begin{align*}' + _strip_top_level_dollar(m.group(1)) + '\\end{align*}',
        tex_content,
        flags=re.DOTALL,
    )
    with tempfile.TemporaryDirectory(dir=str(ROOT)) as tmpdir:
        tmp_path = Path(tmpdir)
        tex_file = tmp_path / "slides.tex"
        tex_file.write_text(tex_content, encoding="utf-8")
        
        # Compile twice to get page count correct
        for _ in range(2):
            res = subprocess.run(
                ["xelatex", "-interaction=nonstopmode", "-output-directory", str(tmp_path), str(tex_file)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(ROOT)
            )
            
        pdf_file = tmp_path / "slides.pdf"
        if not pdf_file.exists():
            print(f"[render_latex] xelatex compilation failed!")
            print(f"[render_latex] LaTeX Source:\n{tex_content}")
            print(f"[render_latex] Compiler Stdout:\n{res.stdout}")
            print(f"[render_latex] Compiler Stderr:\n{res.stderr}")
            log_file = tmp_path / "slides.log"
            if log_file.exists():
                print(f"[render_latex] Compiler Log (last 40 lines):\n")
                lines = log_file.read_text().splitlines()
                print("\n".join(lines[-40:]))
            raise RuntimeError("LaTeX compilation failed.")
            
        # Copy compiled PDF to target output path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(pdf_file), str(out_path))
        
    return out_path
