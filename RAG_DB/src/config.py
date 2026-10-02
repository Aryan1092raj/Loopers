"""Shared paths and settings. All scripts import from here.

Kept intentionally light: no heavy libs imported at module load, so the CLI
starts instantly and `--help` works before models/deps are installed.

LLM providers:
  - Generation: DeepSeek V4 Pro primary, Anthropic/Groq fallback, Gemini last-resort
  - Vision/labeling: Gemini, fallback NVIDIA NIM, local GPU
  - Eval judge: DeepSeek V4 Pro → Gemini
"""
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

def _setup_nvidia_libs():
    """Auto-configure LD_LIBRARY_PATH and Paddle flags with restart trick.

    The os.execve restart trick below is Linux/WSL-only (LD_LIBRARY_PATH,
    execve semantics) and crashes with an access violation on Windows, so it
    no-ops there — Windows has no LD_LIBRARY_PATH/CUDA-NVIDIA-pip layout to
    fix up anyway.
    """
    if sys.platform == "win32":
        return

    restart_needed = False

    if (os.environ.get("FLAGS_allocator_strategy") != "auto_growth" or
        os.environ.get("FLAGS_fraction_of_gpu_memory_to_use") != "0.01" or
        os.environ.get("FLAGS_conv_workspace_size_limit") != "256" or
        os.environ.get("FLAGS_cudnn_exhaustive_search") != "0" or
        os.environ.get("FLAGS_auto_growth_chunk_size_in_mb") != "32"):
        os.environ["FLAGS_allocator_strategy"] = "auto_growth"
        os.environ["FLAGS_fraction_of_gpu_memory_to_use"] = "0.01"
        os.environ["FLAGS_conv_workspace_size_limit"] = "256"
        os.environ["FLAGS_cudnn_exhaustive_search"] = "0"
        os.environ["FLAGS_auto_growth_chunk_size_in_mb"] = "32"
        restart_needed = True

    nvidia_base = os.path.join(str(ROOT), ".venv", "lib", "python3.12", "site-packages", "nvidia")
    if os.path.exists(nvidia_base):
        lib_dirs = []
        for sub in os.listdir(nvidia_base):
            lib_path = os.path.join(nvidia_base, sub, "lib")
            if os.path.exists(lib_path):
                lib_dirs.append(lib_path)
        if lib_dirs:
            ld_path = os.environ.get("LD_LIBRARY_PATH", "")
            missing = [d for d in lib_dirs if d not in ld_path]
            if missing:
                new_ld = ":".join(lib_dirs)
                os.environ["LD_LIBRARY_PATH"] = f"{new_ld}:{ld_path}" if ld_path else new_ld
                restart_needed = True

    # Under uvicorn the re-exec restarts the server process; set LOOPERS_NO_REEXEC=1 there.
    if restart_needed and not os.environ.get("LOOPERS_NO_REEXEC"):
        os.execve(sys.executable, [sys.executable] + sys.argv, os.environ)

_setup_nvidia_libs()
load_dotenv(ROOT / ".env")

RAW = ROOT / "raw"
PROCESSED = ROOT / "processed"
IMAGES = PROCESSED / "images"
FORMULA_DIR = PROCESSED / "formulas"
TABLES_DIR = PROCESSED / "tables"
METADATA = ROOT / "metadata"
QDRANT_DIR = ROOT / "qdrant_db"
OUTPUT = ROOT / "output"

REGISTRY = METADATA / "registry.json"
LABELS = METADATA / "labels.json"
EVAL_QUESTIONS = METADATA / "eval_questions.json"
EMBED_STATE = METADATA / "embed_state.json"  # tracks per-file sha256 for incremental embed

COLLECTION = os.getenv("COLLECTION", "lms_pilot")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")
VECTOR_SIZE = 1024  # BGE-M3 dense dimension

# AgentRouter (Claude Opus proxy)
AGENTROUTER_API_KEY = os.getenv("AGENTROUTER_API_KEY")
AGENTROUTER_MODEL = os.getenv("AGENTROUTER_MODEL", "gpt-5.5")
AGENTROUTER_USER = os.getenv("AGENTROUTER_USER", "cline")

# DeepSeek V4 Pro (primary generation / judge LLM)
# OpenAI-compatible API at https://api.deepseek.com
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
# Optional reasoning_effort for DeepSeek ("low" | "medium" | "high" | ""). When set,
# the API enables thinking mode. JSON spec generation should leave this empty so
# the model answers directly without spending tokens on reasoning (reliable JSON).
DEEPSEEK_REASONING = os.getenv("DEEPSEEK_REASONING", "")

# Revision shorts (src/shorts.py). Condenses existing Lecture.notesJson into one
# PDF, so it runs on Flash rather than the shared DEEPSEEK_MODEL — extraction, not
# reasoning. Trim limits apply before the call and cost nothing.
SHORTS_MODEL = os.getenv("SHORTS_MODEL", "deepseek-v4-flash")
SHORTS_PROSE_CHARS = int(os.getenv("SHORTS_PROSE_CHARS", "400"))
SHORTS_MAX_BULLETS = int(os.getenv("SHORTS_MAX_BULLETS", "6"))
SHORTS_INPUT_BUDGET_TOK = int(os.getenv("SHORTS_INPUT_BUDGET_TOK", "96000"))


def require_deepseek_key() -> str:
    if not DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY required. Set in .env")
    return DEEPSEEK_API_KEY


def require_groq_keys() -> list[str]:
    if len(GROQ_KEYS) < 4:
        raise RuntimeError("Need 4 GROQ_API_KEYs for fallback. Set in .env")
    return GROQ_KEYS


# Native Anthropic API — used directly (no AgentRouter proxy needed) when this
# key is set. Shares NOTES_MODEL with the main LMS's .env if ANTHROPIC_MODEL
# isn't set separately.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL") or os.getenv("NOTES_MODEL", "claude-sonnet-4-6")

# OpenAI (also used as AgentRouter passthrough for gpt-5.5 proxy)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.5-preview")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")

# OCR Configuration
OCR_ENGINE = os.getenv("OCR_ENGINE", "paddle")  # "paddle" or "glm-ocr"
GLM_OCR_MODE = os.getenv("GLM_OCR_MODE", "local")  # "local" or "remote"
GLM_OCR_API_URL = os.getenv("GLM_OCR_API_URL", "http://localhost:11434/v1")
GLM_OCR_API_KEY = os.getenv("GLM_OCR_API_KEY", "EMPTY")
GLM_OCR_MODEL = os.getenv("GLM_OCR_MODEL", "zai-org/GLM-OCR")

# Hybrid retrieval (Phase 1+2)
RRF_K = 60       # RRF stabilizing constant — standard value from literature
SPARSE_K = 50    # sparse BM25 sub-query candidate count
DENSE_K = 50     # dense semantic sub-query candidate count

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
NVIDIA_KEYS = [k.strip() for k in NVIDIA_API_KEY.split(",")] if NVIDIA_API_KEY else []

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_KEYS = [k.strip() for k in GROQ_API_KEY.split(",")] if GROQ_API_KEY else []
GROQ_CONTENT_MODEL = os.getenv("GROQ_CONTENT_MODEL", "llama-3.3-70b-versatile")
def __getattr__(name: str):
    import logging as _logging
    if name == "GROQ_ORCHESTRATOR_KEY":
        if not GROQ_KEYS:
            _logging.warning("config: GROQ_API_KEY missing — GROQ_ORCHESTRATOR_KEY is None")
        return GROQ_KEYS[0] if len(GROQ_KEYS) >= 1 else None
    if name == "GROQ_CONTENT_KEYS":
        if len(GROQ_KEYS) < 2:
            _logging.warning("config: GROQ_API_KEY has fewer than 2 keys — GROQ_CONTENT_KEYS may be empty")
        return GROQ_KEYS[1:3] if len(GROQ_KEYS) >= 3 else (GROQ_KEYS[1:] if len(GROQ_KEYS) >= 2 else GROQ_KEYS)
    if name == "GROQ_TIKZ_KEY":
        if not GROQ_KEYS:
            _logging.warning("config: GROQ_API_KEY missing — GROQ_TIKZ_KEY is None")
        return GROQ_KEYS[3] if len(GROQ_KEYS) >= 4 else (GROQ_KEYS[-1] if GROQ_KEYS else None)
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


# File type buckets keyed off extension.
PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".webp"}
MD_EXTS = {".md", ".markdown"}
PPTX_EXTS = {".pptx"}


def classify(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in PDF_EXTS:
        return "pdf"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in MD_EXTS:
        return "md"
    if ext in PPTX_EXTS:
        return "pptx"
    return "other"


def require_nvidia_key() -> str:
    """Raise SystemExit if NVIDIA_KEYS is empty."""
    if not NVIDIA_KEYS or not NVIDIA_KEYS[0]:
        raise SystemExit(
            "NVIDIA_API_KEY not set. Copy .env.example to .env and fill it in."
        )
    return NVIDIA_KEYS[0]


def require_groq_key() -> str:
    """Raise if GROQ_KEYS is empty.

    Deliberately a plain exception, not SystemExit — SystemExit inherits from
    BaseException, so `except Exception` in the LLM fallback chains (llm.py,
    generate.py) can't catch it, and one missing optional key would hard-kill
    the whole pipeline instead of falling through to the next provider.
    """
    if not GROQ_KEYS or not GROQ_KEYS[0]:
        raise ValueError(
            "GROQ_API_KEY not set. Copy .env.example to .env and fill it in."
        )
    return GROQ_KEYS[0]


def require_gemini_key() -> str:
    """Raise if GEMINI_API_KEY is missing (see require_groq_key for why not SystemExit)."""
    if not GEMINI_API_KEY:
        raise ValueError(
            "GEMINI_API_KEY not set. Copy .env.example to .env and fill it in."
        )
    return GEMINI_API_KEY


# ---------------------------------------------------------------------------
# Multi-subject infrastructure
# ---------------------------------------------------------------------------

KNOWN_SUBJECTS = [
    "mathematics", "physics", "chemistry", "biology",
    "history", "economics", "geography", "computer_science",
    "literature", "english", "hindi", "psychology",
    "sociology", "statistics", "astronomy", "anatomy",
    "political_science",
]

# Short aliases → canonical subject (so raw/math/ maps to "mathematics")
_SUBJECT_ALIASES = {
    "math": "mathematics", "maths": "mathematics", "calc": "mathematics",
    "phy": "physics", "phys": "physics",
    "chem": "chemistry",
    "bio": "biology",
    "hist": "history",
    "econ": "economics", "eco": "economics",
    "geo": "geography",
    "cs": "computer_science", "compsci": "computer_science",
    "lit": "literature", "eng": "english",
    "psych": "psychology", "soc": "sociology",
    "stats": "statistics", "stat": "statistics",
    "astro": "astronomy", "anat": "anatomy",
    "polsci": "political_science",
}


def normalize_subject(name: str) -> str:
    """Map a directory/filename prefix to a canonical subject name."""
    key = name.lower().strip().replace("-", "_").replace(" ", "_")
    if key in KNOWN_SUBJECTS:
        return key
    return _SUBJECT_ALIASES.get(key, key)


def discover_subjects() -> list[str]:
    """Scan raw/ for subdirectories — each is a subject.

    Returns sorted list of canonical subject names.
    Always includes "general" for files sitting directly in raw/.
    """
    subjects = set()
    if RAW.exists():
        for child in RAW.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                subjects.add(normalize_subject(child.name))
        # Check for loose files in raw/ root
        for child in RAW.iterdir():
            if child.is_file() and not child.name.startswith("."):
                subjects.add("general")
                break
    return sorted(subjects)


def subject_raw_dir(subject: str) -> Path:
    """Return the raw/ subdirectory for a subject. Creates it if needed."""
    if subject == "general":
        return RAW
    d = RAW / subject
    d.mkdir(parents=True, exist_ok=True)
    return d


def subject_for_file(filepath: Path) -> str:
    """Determine subject from a file's location relative to raw/.

    raw/physics/textbook.pdf  → "physics"
    raw/textbook.pdf          → "general"
    """
    try:
        rel = filepath.resolve().relative_to(RAW.resolve())
        parts = rel.parts
        if len(parts) > 1:
            return normalize_subject(parts[0])
    except (ValueError, IndexError):
        pass
    return "general"


# ---------------------------------------------------------------------------
# Unicode -> LaTeX sanitizer (shared between render_latex.py and viz.py)
#
# LLM-written TikZ/content routinely contains raw Unicode glyphs (bullets,
# arrows, Greek, math relations). pdflatex + [T1]fontenc cannot typeset these
# ("Unicode character X not set up for use with LaTeX") and the whole diagram
# compile fails. Map the common offenders to LaTeX macros. \ensuremath{} makes
# each replacement valid in BOTH text and math mode, so we can run this blindly
# over mixed TikZ code without tracking which mode we're in.
# ---------------------------------------------------------------------------

_UNICODE_LATEX = {
    # bullets / dots / marks
    "•": r"\ensuremath{\bullet}", "●": r"\ensuremath{\bullet}",
    "○": r"\ensuremath{\circ}", "◦": r"\ensuremath{\circ}",
    "∙": r"\ensuremath{\cdot}", "·": r"\ensuremath{\cdot}",
    "✓": r"\ensuremath{\checkmark}", "✔": r"\ensuremath{\checkmark}",
    "✗": r"\ensuremath{\times}", "✘": r"\ensuremath{\times}",
    "★": r"\ensuremath{\star}", "☆": r"\ensuremath{\star}",
    # arrows
    "→": r"\ensuremath{\rightarrow}", "←": r"\ensuremath{\leftarrow}",
    "↔": r"\ensuremath{\leftrightarrow}", "⇒": r"\ensuremath{\Rightarrow}",
    "⇐": r"\ensuremath{\Leftarrow}", "⇔": r"\ensuremath{\Leftrightarrow}",
    "↑": r"\ensuremath{\uparrow}", "↓": r"\ensuremath{\downarrow}",
    "⟶": r"\ensuremath{\longrightarrow}", "⟵": r"\ensuremath{\longleftarrow}",
    "↦": r"\ensuremath{\mapsto}",
    # math relations / operators
    "×": r"\ensuremath{\times}", "÷": r"\ensuremath{\div}",
    "−": r"\ensuremath{-}", "±": r"\ensuremath{\pm}",
    "∓": r"\ensuremath{\mp}", "≤": r"\ensuremath{\leq}",
    "≥": r"\ensuremath{\geq}", "≠": r"\ensuremath{\neq}",
    "≈": r"\ensuremath{\approx}", "≡": r"\ensuremath{\equiv}",
    "∞": r"\ensuremath{\infty}", "√": r"\ensuremath{\sqrt{}}",
    "∑": r"\ensuremath{\sum}", "∏": r"\ensuremath{\prod}",
    "∫": r"\ensuremath{\int}", "∂": r"\ensuremath{\partial}",
    "∇": r"\ensuremath{\nabla}", "∝": r"\ensuremath{\propto}",
    "∈": r"\ensuremath{\in}", "∉": r"\ensuremath{\notin}",
    "⊆": r"\ensuremath{\subseteq}", "⊂": r"\ensuremath{\subset}",
    "∪": r"\ensuremath{\cup}", "∩": r"\ensuremath{\cap}",
    "∀": r"\ensuremath{\forall}", "∃": r"\ensuremath{\exists}",
    "°": r"\ensuremath{^\circ}",
    # Greek (lower)
    "α": r"\ensuremath{\alpha}", "β": r"\ensuremath{\beta}",
    "γ": r"\ensuremath{\gamma}", "δ": r"\ensuremath{\delta}",
    "ε": r"\ensuremath{\epsilon}", "θ": r"\ensuremath{\theta}",
    "λ": r"\ensuremath{\lambda}", "μ": r"\ensuremath{\mu}",
    "π": r"\ensuremath{\pi}", "ρ": r"\ensuremath{\rho}",
    "σ": r"\ensuremath{\sigma}", "τ": r"\ensuremath{\tau}",
    "φ": r"\ensuremath{\phi}", "ω": r"\ensuremath{\omega}",
    "ψ": r"\ensuremath{\psi}", "χ": r"\ensuremath{\chi}",
    # Greek (upper)
    "Γ": r"\ensuremath{\Gamma}", "Δ": r"\ensuremath{\Delta}",
    "Θ": r"\ensuremath{\Theta}", "Λ": r"\ensuremath{\Lambda}",
    "Π": r"\ensuremath{\Pi}", "Σ": r"\ensuremath{\Sigma}",
    "Φ": r"\ensuremath{\Phi}", "Ω": r"\ensuremath{\Omega}",
    "Ψ": r"\ensuremath{\Psi}",
    # punctuation / spaces that break pdflatex
    "…": r"\ensuremath{\ldots}", "‘": "`", "’": "'",
    "“": "``", "”": "''", "–": "--", "—": "---",
    " ": " ", " ": " ", "​": "", "﻿": "",
    "½": r"\ensuremath{\tfrac{1}{2}}", "¼": r"\ensuremath{\tfrac{1}{4}}",
    "¾": r"\ensuremath{\tfrac{3}{4}}",
    "²": r"\ensuremath{^2}", "³": r"\ensuremath{^3}", "¹": r"\ensuremath{^1}",
}


# Regex for token-shaped math fragments the LLM leaves inside prose text:
#   - "c^T", "x_B", "B^{-1}", "x_{ij}", "r_N", "n-2", "x 1", "a^T_j", "x_i^2"
#   - also "(2, 3)^T", "(a, b)^{-1}" trailing a close-paren / bracket
#   - also plain "r3 = 7" (letter+digit when used as math variable)
# When detected, wrap each match in $...$ so pdflatex renders it as math instead
# of escaping the caret/underscore into literal "^\textasciicircum{}" gibberish.
# Order matters: longer combined patterns (subscript+superscript) before
# the single-subscript or single-superscript forms so greedy match doesn't
# break "c_B^T" into "c_B" + orphaned "^T".
_INLINE_MATH_TOKEN = re.compile(
    r"(?<![\\$])"                                          # not preceded by \ or $
    r"(?:"
    # combined trailing-paren superscript with the parens: "(2, 3)^T", "(a, b)^{-1}"
    # Captures the preceding content inside the parens too so the wrap is balanced.
    r"\([^()]*\)\^[A-Za-z0-9{][A-Za-z0-9{}\-]*"
    r"|"
    # combined with trailing subscript too: x_T^T, a_{j}^T_i, c_B^{-1}, x_i^2
    r"\b[A-Za-z](?:_[A-Za-z0-9{][A-Za-z0-9,}\-]*)?"
    r"\^[A-Za-z0-9{][A-Za-z0-9{}\-]*"
    r"(?:_[A-Za-z0-9{][A-Za-z0-9,}\-]*)?"
    r"|"
    # bare superscript: c^T, B^{-1}
    r"\b[A-Za-z]\^[A-Za-z0-9{][A-Za-z0-9{}\-]*"
    r"|"
    # bare subscript: r_N, x_{ij}, x_1, r_5, a_ij
    r"\b[A-Za-z]_[A-Za-z0-9{][A-Za-z0-9,}\-]*"
    r"|"
    # plain letter-digit math variable in equation-like contexts.
    # Only wrap when followed by '=' or '(' or ',' or end-of-clause punctuation
    # so we don't grab English words like "x3" in "x3 ray" or product names.
    r"\b[A-Za-z]\d+(?=\s*[=,;\)\]])"
    r"|"
    # letter-digit pair with hyphen: n-2, x-1 (both ASCII and Unicode minus)
    r"\b[A-Za-z][\-−](\d+)"
    r")"
)


def wrap_inline_math(text: str) -> str:
    """Wrap obvious math fragments in prose with $...$ delimiters.

    The LLM frequently writes things like 'c^T x', 'r_N', 'x_{ij}', 'n-2' inside
    the `prose` field instead of in `math_blocks`. Without wrapping, pdflatex's
    text-mode escape turns '^' into '\\textasciicircum{}' and '_' into '\\_',
    producing the literal "c^T x" / "r_N" gibberish seen in earlier PDFs.

    Conservative: only matches single-letter variable + caret/underscore + token.
    Safe to call before sanitize_unicode / tex_escape.
    """
    if not text:
        return text
    return _INLINE_MATH_TOKEN.sub(lambda m: "$" + m.group(0) + "$", text)


def sanitize_unicode(text: str) -> str:
    """Replace some raw Unicode glyphs with LaTeX macros.

    Safe in both text and math mode (\\ensuremath). Leaves all other Unicode
    (like Devanagari) intact for xelatex to handle natively.
    """
    if not text:
        return text
    for uni, latex in _UNICODE_LATEX.items():
        if uni in text:
            text = text.replace(uni, latex)
    return text


# ---------------------------------------------------------------------------
# Visual palette (shared between render_latex.py and viz.py)
# ---------------------------------------------------------------------------

PALETTE_DEFS = r"""
\definecolor{brandDark}{HTML}{1A252C}
\definecolor{brandTeal}{HTML}{006D77}
\definecolor{brandMint}{HTML}{83C5BE}
\definecolor{waterBlue}{HTML}{00B4D8}
\definecolor{waterDeep}{HTML}{0077B6}
\definecolor{earthBrown}{HTML}{DDA15E}
\definecolor{earthDark}{HTML}{BC6C25}
\definecolor{accentOrange}{HTML}{E76F51}
\definecolor{lightBg}{HTML}{F8F9FA}
\definecolor{forestGreen}{HTML}{2A9D8F}
\definecolor{paleYellow}{HTML}{FFFACD}  % ponytail: Claude Opus hallucinates pale yellow
"""
