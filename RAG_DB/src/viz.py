"""Generate figures from LLM-written code (tikz and matplotlib).

Called by generate.py after spec is produced. Converts 'tikz' and 'matplotlib'
slides into 'image' slides by executing their code and rendering to PNG.
Both types run in parallel via ThreadPoolExecutor.
"""
import base64
import httpx
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor


def _run_matplotlib(code: str, out_path: Path) -> bool:
    """Execute matplotlib code in isolated subprocess. Returns True on success."""
    # Wrap user code: backend, imports, tight_layout, save
    script = textwrap.dedent(f"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
try:
    plt.style.use('seaborn-v0_8-whitegrid')
except Exception:
    pass
{code}
plt.tight_layout()
plt.savefig({str(out_path)!r}, dpi=150, bbox_inches='tight')
plt.close('all')
""")
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            timeout=15,
            check=True,
            capture_output=True,
        )
        return out_path.exists()
    except Exception as e:
        print(f"[viz] matplotlib failed for {out_path.name}: {e}")
        return False


def sanitize_graphviz_code(code: str) -> str:
    """Convert single-quoted node/edge labels to double-quoted ones.

    DOT only accepts double-quoted strings inside [label='...']. The LLM emits
    single quotes because the notes prompt forbids double quotes inside JSON
    strings, but `dot` then chokes on the syntax:
        Error: syntax error in line N near '''
    This wraps each `label='...'` in double quotes, escaping embedded quotes.
    """
    out = re.sub(
        r"label\s*=\s*'([^']*)'",
        lambda m: 'label="' + m.group(1).replace('"', '\\"') + '"',
        code,
    )
    # Also fix standalone node labels written as node['text'] without label=
    out = re.sub(
        r"\[([^\]]*?)'([^']+)'([^\]]*?)\]",
        lambda m: '[' + m.group(1) + '"' + m.group(2).replace('"', '\\"') + '"' + m.group(3) + ']',
        out,
    )
    return out


def sanitize_mermaid_code(code: str) -> str:
    """Helper to ensure Mermaid node labels are wrapped in double quotes to prevent syntax errors."""
    lines = []
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("%%") or stripped.startswith("graph ") or stripped.startswith("flowchart ") or stripped.startswith("sequenceDiagram") or stripped.startswith("stateDiagram"):
            lines.append(line)
            continue
            
        first_open = -1
        first_shape = None
        for shape_open, shape_close in [('([', '])'), ('[[', ']]'), ('((', '))'), ('{{', '}}'), ('[', ']'), ('{', '}'), ('(', ')')]:
            idx = stripped.find(shape_open)
            if idx > 0 and (stripped[idx-1].isalnum() or stripped[idx-1] == '_'):
                if first_open == -1 or idx < first_open:
                    first_open = idx
                    first_shape = (shape_open, shape_close)
        
        if first_open != -1:
            shape_open, shape_close = first_shape
            node_id = stripped[:first_open].strip()
            rest = stripped[first_open + len(shape_open):]
            last_close = rest.rfind(shape_close)
            if last_close != -1:
                content = rest[:last_close].strip()
                after = rest[last_close + len(shape_close):]
                
                if not (content.startswith('"') and content.endswith('"')):
                    if (content.startswith("'") and content.endswith("'")) or (content.startswith('"') and content.endswith('"')):
                        content = content[1:-1]
                    content_escaped = content.replace('"', '\\"')
                    indent = line[:len(line) - len(stripped)]
                    line = f"{indent}{node_id}{shape_open}\"{content_escaped}\"{shape_close}{after}"
        
        lines.append(line)
    return "\n".join(lines)


def _simplify_mermaid_code(code: str) -> str:
    """Strip complex math/special chars from mermaid labels for API rendering."""
    import re
    lines = []
    for line in code.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        stripped = re.sub(r'\{[^}]*\}', '', stripped)
        stripped = re.sub(r'\\[a-zA-Z]+', '', stripped)
        lines.append(stripped)
    return '\n'.join(lines)


def _run_mermaid(code: str, out_path: Path) -> bool:
    """Render Mermaid syntax to PNG using mermaid.ink API."""
    attempts = [
        ("original", lambda: sanitize_mermaid_code(code.strip())),
        ("simplified", lambda: _simplify_mermaid_code(sanitize_mermaid_code(code.strip()))),
    ]
    for label, prepare in attempts:
        try:
            prepared = prepare()
            if prepared.startswith("```"):
                lines = prepared.splitlines()
                prepared = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:]).strip()
            code_bytes = prepared.encode("utf-8")
            base64_bytes = base64.b64encode(code_bytes)
            base64_str = base64_bytes.decode("utf-8")
            base64url_str = base64_str.replace("+", "-").replace("/", "_").rstrip("=")
            url = f"https://mermaid.ink/img/{base64url_str}"
            with httpx.Client(timeout=30) as client:
                r = client.get(url)
                r.raise_for_status()
                out_path.write_bytes(r.content)
            if out_path.exists():
                return True
        except Exception as e:
            if label == "original" and "400" in str(e):
                print(f"[viz] mermaid 400 for {out_path.name}; retrying with simplified labels...")
                continue
            print(f"[viz] mermaid failed for {out_path.name} ({label}): {e}")
            return False
    return False


def _run_graphviz(code: str, out_path: Path) -> bool:
    """Render Graphviz DOT syntax to PNG."""
    try:
        code = code.strip()
        if code.startswith("```"):
            lines = code.splitlines()
            code = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:]).strip()
        code = sanitize_graphviz_code(code)
        subprocess.run(
            ["dot", "-Tpng", "-o", str(out_path)],
            input=code.encode("utf-8"),
            timeout=15,
            check=True,
            capture_output=True,
        )
        return out_path.exists()
    except Exception as e:
        print(f"[viz] graphviz failed for {out_path.name}: {e}")
        return False


def _balance_braces(code: str) -> str:
    """Append missing closing braces so xelatex doesn't choke on LLM output."""
    depth = 0
    i = 0
    while i < len(code):
        if code[i] == '\\':
            i += 2  # skip escaped char
            continue
        if code[i] == '{':
            depth += 1
        elif code[i] == '}':
            depth -= 1
        i += 1
    if depth > 0:
        code += '}' * depth
    return code


def _sanitize_tikz(code: str) -> str:
    """Strip backslashes from unknown words and balance math delimiters."""
    _KNOWN = {
        "draw", "node", "fill", "path", "pattern", "shade", "clip",
        "coordinate", "pic", "foreach", "matrix",
        "bfseries", "small", "footnotesize", "tiny", "large", "Large",
        "LARGE", "huge", "Huge", "normalsize",
        "textbf", "textit", "emph", "underline", "texttt", "textsf",
        "textsc", "textrm", "textsl", "textup", "textcolor", "color",
        "begin", "end",
        "usepackage", "documentclass", "usetikzlibrary", "definecolor",
        "providecolor", "providecommand", "newcommand", "renewcommand",
        "sin", "cos", "tan", "log", "ln", "exp", "lim", "sum", "prod",
        "int", "iint", "iiint", "oint", "sqrt", "frac",
        "to", "rightarrow", "leftarrow", "Rightarrow", "Leftarrow",
        "cdot", "times", "div", "pm", "mp", "infty", "partial", "nabla",
        "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon",
        "zeta", "eta", "theta", "vartheta", "iota", "kappa", "lambda",
        "mu", "nu", "xi", "omicron", "pi", "varpi", "rho", "varrho",
        "sigma", "varsigma", "tau", "upsilon", "phi", "varphi", "chi",
        "psi", "omega", "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi",
        "Sigma", "Phi", "Psi", "Omega",
        "text", "mbox", "tikzset",
        "centering", "raggedright", "raggedleft",
        # Vector/accent decorations — missing here silently stripped commands
        # like "\vec{F}" down to bare "vecF" (backslash gone), which then
        # renders as plain letters instead of the intended notation.
        "vec", "hat", "bar", "dot", "ddot", "tilde", "breve", "check",
        "overline", "underline", "overrightarrow", "overleftarrow",
        "overleftrightarrow", "widehat", "widetilde",
        "binom", "dbinom", "stackrel", "substack",
        "left", "right", "big", "Big", "bigg", "Bigg",
        "quad", "qquad", "phantom", "hphantom", "vphantom",
        "mathbf", "mathrm", "mathbb", "mathcal", "mathfrak", "mathit",
        "mathsf", "mathtt", "boldsymbol", "operatorname", "ensuremath",
        "displaystyle", "textstyle", "scriptstyle", "scriptscriptstyle",
        "limits", "nolimits", "notag", "nonumber",
    }

    def _replace(m: re.Match) -> str:
        word = m.group(1)
        return word if word not in _KNOWN else m.group(0)

    code = re.sub(r'\\([A-Za-z]+)', _replace, code)

    opens = code.count(r'\(')
    closes = code.count(r'\)')
    if opens > closes:
        code += r'\)' * (opens - closes)

    return code


def _tex_engine() -> str | None:
    """xelatex primary (Unicode is handled by sanitize_unicode upstream);
    lualatex only if xelatex is absent. None if neither present."""
    import shutil
    for eng in ("xelatex", "lualatex"):
        if shutil.which(eng):
            return eng
    return None


def _run_tikz(code: str, out_path: Path) -> bool:
    """Compile standalone tikzpicture to PNG. Sanitizes Unicode first so the
    LLM's raw glyphs (bullets, arrows, Greek) don't crash the compile, then
    renders via lualatex/xelatex + pdftoppm/convert."""
    engine = _tex_engine()
    if not engine:
        print(f"[viz] Warning: no LaTeX engine (lualatex/xelatex) on path. TikZ->PNG skipped for {out_path.name}.")
        return False

    try:
        from config import PALETTE_DEFS, sanitize_unicode
    except ImportError:
        PALETTE_DEFS = ""
        sanitize_unicode = lambda s: s

    code = sanitize_unicode(_sanitize_tikz(_balance_braces(code)))
    code = re.sub(r'\\begin\{tikzpicture\}(?:\[.*?\])?', '', code)
    code = re.sub(r'\\end\{tikzpicture\}', '', code)
    code = code.strip()
    tex = textwrap.dedent(rf"""
\documentclass[tikz,border=6pt]{{standalone}}
\usepackage{{amsmath}}
\usepackage{{amssymb}}
\usepackage{{tikz}}
\usetikzlibrary{{shapes.geometric,arrows.meta,positioning,calc,
  backgrounds,shadows,decorations.pathmorphing,patterns,fadings}}
\usepackage{{xcolor}}
{PALETTE_DEFS}
\begin{{document}}
\begin{{tikzpicture}}
{code}
\end{{tikzpicture}}
\end{{document}}
""")

    tex_path = out_path.with_suffix(".tex")
    pdf_path = out_path.with_suffix(".pdf")
    tex_path.write_text(tex, encoding="utf-8")

    try:
        subprocess.run(
            [engine, "-interaction=nonstopmode",
             "-output-directory", str(out_path.parent),
             str(tex_path)],
            timeout=60,
            check=True,
            capture_output=True,
            cwd=str(out_path.parent),
        )
    except subprocess.CalledProcessError as e:
        # -interaction=nonstopmode often exits non-zero (undefined control
        # sequence, PGF math error, etc.) yet still writes a usable PDF page —
        # don't discard the diagram if that happened, just log the warning.
        if not pdf_path.exists():
            log_content = ""
            log_path = out_path.with_suffix(".log")
            if log_path.exists():
                log_content = log_path.read_text(errors="ignore")[-1000:]
            stdout_content = e.stdout.decode(errors="ignore")[-1000:]
            print(f"[viz] xelatex failed for {out_path.name}:\n--- LOG ---\n{log_content}\n--- STDOUT ---\n{stdout_content}")
            return False
        print(f"[viz] xelatex reported errors for {out_path.name} but produced a PDF anyway — using it.")
    except Exception as e:
        print(f"[viz] xelatex error for {out_path.name}: {e}")
        return False

    if not pdf_path.exists():
        print(f"[viz] xelatex produced no PDF for {out_path.name}")
        return False

    # Convert PDF → PNG (try pdftoppm first, then ImageMagick convert)
    converted = False
    for cmd in [
        ["pdftoppm", "-r", "150", "-png", "-singlefile", str(pdf_path), str(out_path.with_suffix(""))],
        ["convert", "-density", "150", str(pdf_path), str(out_path)],
        # Ghostscript fallback — commonly installed even when poppler isn't
        ["gs", "-dNOPAUSE", "-dBATCH", "-sDEVICE=png16m", "-r200",
         f"-sOutputFile={out_path}", str(pdf_path)],
    ]:
        try:
            subprocess.run(cmd, timeout=20, check=True, capture_output=True)
            # pdftoppm writes <stem>-1.png or <stem>.png depending on version
            if not out_path.exists():
                candidate = out_path.parent / f"{out_path.stem}-1.png"
                if candidate.exists():
                    candidate.rename(out_path)
            if out_path.exists():
                converted = True
                break
        except Exception:
            continue

    if not converted:
        try:
            import fitz
            doc = fitz.open(str(pdf_path))
            pix = doc[0].get_pixmap(dpi=200)
            pix.save(str(out_path))
            doc.close()
            converted = True
        except Exception:
            pass

    if not converted:
        print(f"[viz] PDF→PNG conversion failed for {out_path.name} (tried pdftoppm, convert, pymupdf)")

    # Always clean up compile artifacts — a failed conversion shouldn't leave
    # an orphaned .pdf sitting in OUTPUT where it gets swept up as a "download".
    for ext in (".tex", ".aux", ".log", ".pdf"):
        out_path.with_suffix(ext).unlink(missing_ok=True)

    return out_path.exists()


def process_slides(slides: list[dict], output_dir: Path, repair_callback=None) -> list[dict]:
    """Convert 'matplotlib', 'tikz', 'mermaid', and 'graphviz' slides → 'image' slides in parallel.

    Slides that fail to render are replaced with a bullets slide from caption/explains so the rest
    of the deck still compiles cleanly.
    """
    targets = [
        (i, s) for i, s in enumerate(slides)
        if s.get("type") in ("matplotlib", "tikz", "mermaid", "graphviz")
    ]
    if not targets:
        return slides

    output_dir.mkdir(parents=True, exist_ok=True)

    def _process(item):
        i, s = item
        slide_type = s["type"]
        out = output_dir / f"viz_{i:02d}.png"
        code = s.get("code", "")

        error_msg = ""
        success = False
        if not code.strip():
            success = False
            error_msg = "Empty diagram code."
        elif slide_type == "matplotlib":
            success = _run_matplotlib(code, out)
            if not success:
                error_msg = "Matplotlib execution failed."
        elif slide_type == "tikz":
            success = _run_tikz(code, out)
            if not success:
                log_path = out.with_suffix(".log")
                error_msg = log_path.read_text(errors="ignore")[-800:] if log_path.exists() else "xelatex compilation failed."
        elif slide_type == "mermaid":
            success = _run_mermaid(code, out)
            if not success:
                error_msg = "Mermaid HTTP API rendering failed."
        elif slide_type == "graphviz":
            success = _run_graphviz(code, out)
            if not success:
                error_msg = "Graphviz dot command rendering failed."

        if not success and repair_callback:
            print(f"[viz] {slide_type} diagram failed compile. Invoking self-healing...")
            fixed_code = repair_callback(slide_type, code, error_msg)
            if fixed_code and fixed_code != code:
                print(f"[viz] Retrying {slide_type} diagram with fixed code...")
                if slide_type == "matplotlib":
                    success = _run_matplotlib(fixed_code, out)
                elif slide_type == "tikz":
                    success = _run_tikz(fixed_code, out)
                elif slide_type == "mermaid":
                    success = _run_mermaid(fixed_code, out)
                elif slide_type == "graphviz":
                    success = _run_graphviz(fixed_code, out)
                if success:
                    code = fixed_code

        if success:
            return i, {
                "type": "image",  # promote to image so render_latex uses \includegraphics
                "title": s.get("title", ""),
                "code": code,
                "image": str(out),
                "caption": s.get("caption", ""),
                "source": s.get("source", {}),
            }
        # Graceful degradation: keep the pedagogy, drop the broken graphic.
        # Turn the diagram's caption/explains into a real bullets slide so the
        # student still gets the concept instead of a "[rendering failed]" sign.
        print(f"[viz] slide {i} ({slide_type}) failed — converting to bullets from caption")
        bullets = [b for b in s.get("explains", []) if str(b).strip()]
        caption = str(s.get("caption", "")).strip()
        if caption:
            bullets.insert(0, caption)
        if not bullets:
            bullets = [f"Refer to the textbook figure: {s.get('title', 'diagram')}."]
        return i, {
            "type": "bullets",
            "title": s.get("title", "Diagram"),
            "bullets": bullets,
            "source": s.get("source", {}),
        }

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(_process, targets))

    for i, replacement in results:
        slides[i] = replacement

    return slides
