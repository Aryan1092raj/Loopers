"""Phase 5b — RAG notes generation.

Retrieves chunks for a topic, generates a structured notes spec via LLM,
renders to a PDF article suitable for student exam preparation.

Generation runs in three passes:
  Pass A — Outline.  One small LLM call produces the section list with
            per-section focus, diagram kind, and example count.
  Pass B — Drafts.   One focused LLM call per section, with retrieval
            filtered to that section's focus. Each call writes >=3 paragraphs
            of prose, math blocks, examples, and (if requested) diagram code.
  Pass C — Assemble. Concatenate per-section drafts into the spec dict shape
            the renderer already expects.

After Pass B we enforce a diagram quota (>=3 sections with a diagram) and a
top-up pass injects DB images surfaced from the topic-level retrieval.
"""
import json
from pathlib import Path

from config import OUTPUT

NOTES_SUBJECT_ADDONS = {
    "mathematics": """
SUBJECT: MATHEMATICS
- Explain every theorem, formula, and definition rigorously.
- Every major theorem MUST have a complete statement and proof or derivation sketch in the `math_blocks` and `prose`.
- Include precise notation. Always define variables immediately before or after equations.
- Use `tikz_code` or `matplotlib_code` for: coordinate plots, number lines, geometric constructions, function graphs, sequences, or set diagrams.
- Include step-by-step worked solutions for mathematical examples.
""",
    "physics": """
SUBJECT: PHYSICS
- State physical laws and principles clearly along with their domain of validity.
- Include detailed derivations of formulas (e.g., equations of motion, work-energy theorem) using math blocks.
- Define every physical variable, its symbol, and its SI units.
- Use `tikz_code` or `matplotlib_code` for: free body diagrams, circuit schematics, ray optics, vector addition, or wave/signal graphs.
""",
    "chemistry": """
SUBJECT: CHEMISTRY
- Write chemical equations and balanced reactions using standard notation.
- Include derivations of thermodynamic, kinetic (rate laws), or equilibrium expressions.
- Use `tikz_code` or `mermaid_code` for: energy profiles, Lewis structures, molecular geometries, or experimental setups (e.g., titration curves).
""",
    "biology": """
SUBJECT: BIOLOGY
- Focus on clear process flowcharts (e.g., DNA replication, protein synthesis) and structural diagrams.
- Use `mermaid_code` or `tikz_code` for: cell organelles, biological pathways, food webs, or energy pyramids.
- Include precise definitions and etymology of biological terms.
""",
    "computer_science": """
SUBJECT: COMPUTER SCIENCE
- Focus on algorithms, data structures, and complexity analysis.
- Every algorithm must be accompanied by its time/space complexity analysis (Big-O derivation).
- Use `mermaid_code` or `graphviz_code` for: trees, graphs, flowcharts of execution, memory layouts, or state machines.
- Include clear code snippets or pseudocode in the prose or examples.
""",
    "geography": """
SUBJECT: GEOGRAPHY / EARTH SCIENCE
- Focus on geographical processes, layers, cycles, and physical structures.
- Use `tikz_code` or `mermaid_code` for: rock cycle, hydrological cycle, atmospheric layers, or soil profiles.
- Use worked examples for geographic calculations (e.g., map scales, time zone differences).
""",
    "history": """
SUBJECT: HISTORY
- Organize concepts chronologically and causally.
- Use `tikz_code` or `mermaid_code` for: timelines, cause-and-effect flowcharts, or historical hierarchy charts.
- Quote primary source excerpts in the prose where relevant.
"""
}

NOTES_SYSTEM_PROMPT = """You are a senior university professor writing a single dense, exam-ready textbook chapter for a student preparing for tests. Your prose reads like a published chapter: every claim is justified, every formula is set in display math, every diagram is self-contained.

You produce ONLY a single JSON object (no markdown, no commentary, no preamble). Start your response with `{` and end with `}`.

Output JSON shape:
{
  "title": str,                                 // chapter title, e.g. "Linear Programming and the Simplex Method"
  "subject": str,                               // subject area, e.g. "mathematics"
  "sections": [
    {
      "heading": str,                           // section title, e.g. "The Feasible Region"
      "prose": str,                             // 3-5 full paragraphs. PLAIN ENGLISH ONLY. No math notation.
      "math_blocks": [str],                     // LaTeX display math expressions (no $$ or \\[ delimiters). All math goes here.
      "tikz_code": str,                         // optional tikzpicture body code (excluding \\begin{tikzpicture} and \\end{tikzpicture}), or ""
      "mermaid_code": str,                      // optional mermaid diagram code, or ""
      "graphviz_code": str,                     // optional graphviz DOT code, or ""
      "matplotlib_code": str,                   // optional matplotlib python code, or ""
      "image": str,                             // optional path to a database image from AVAILABLE IMAGES, or ""
      "caption": str,                           // caption for whichever diagram/image you choose, or ""
      "examples": [ {"problem": str, "solution": [str]} ]
    }
  ]
}

HARD RULES (your output will be rejected if any are violated):

1. PROSE FIELD IS PLAIN ENGLISH ONLY.
   The `prose` field is rendered as pdflatex text-mode. ANY math notation in `prose` becomes gibberish.
   - DO NOT write `c^T x`, `x_B`, `B^{-1}`, `r_N`, `>=`, `<=`, `alpha`, `sigma`, etc. in prose.
   - DO write: "the transpose of c times x", "x sub B", "B inverse", "r sub N", "at least", "less than or equal to", "alpha", "sigma".
   - ALL equations, variables with sub/superscripts, and Greek letters belong in `math_blocks`. Use \\ge, \\le, \\alpha, x_B, c^T, B^{-1} etc. inside math_blocks.

2. JSON SYNTAX RULES — OUTPUT VALID JSON.
   - ALL property names and string values MUST use DOUBLE QUOTES (`"`). This is the JSON standard.
   - Inside string values: escape double quotes as `\"`, backslashes as `\\`, newlines as `\n`.
   - Example: a mermaid label `A["Start"]` becomes `"A[\\"Start\\"]"` in JSON.
   - For graphviz labels use single quotes inside the DOT code: `label='Primal LP'` — these do NOT need escaping in JSON.
   - Do NOT wrap output in markdown fences (no ```). Start directly with `{` and end with `}`.

3. DIAGRAM COMPLETENESS RULES.
   A half-finished diagram is worse than no diagram. Every diagram you emit must be SELF-CONTAINED and COMPILE-READY.

   - `tikz_code`: provide a FULL tikzpicture body with explicit axes, gridlines if appropriate, labelled points, shaded regions, and a clear caption-worthy structure. Use `\\draw`, `\\node`, `\\fill` consistently. Math inside TikZ must use `$...$`. Do not start with `\\begin{tikzpicture}`.
   - `mermaid_code`: emit the FULL mermaid definition starting with `flowchart`, `graph`, `sequenceDiagram`, etc. ALL node labels must use double quotes (e.g. `A["Start"]`). NEVER use parentheses `()` around node labels without quotes — mermaid.ink rejects them.
   - `graphviz_code`: emit the FULL DOT definition starting with `digraph` or `graph`. ALL `[label='...']` MUST use single quotes around label text — the renderer auto-converts them to double quotes for dot. Example: `Primal [label='Primal LP']` (NOT `label="..."`).
   - `matplotlib_code`: emit FULL python that ends with `plt.show()` or `plt.savefig(...)` omitted — the renderer wraps it. Include imports inline if needed (the renderer provides numpy as np and matplotlib.pyplot as plt). Avoid `plt.style.use(...)` calls.

4. CONTENT RULES.
   - Minimum 6 sections. Each section's prose is >=3 full paragraphs (>=400 words). Cover definitions, intuitions, derivations, and at least one concrete application.
   - Every theorem / law / algorithm gets a full statement + derivation in `math_blocks`.
   - >=3 sections include a worked example with step-by-step solution (each step as a separate string in `solution`).
   - >=3 sections include a diagram (tikz / mermaid / graphviz / matplotlib).
   - Ground every fact in the retrieved CONTEXT. Do NOT invent content.

5. EXAMPLES.
   - `problem` is a complete, self-contained problem statement with all data.
   - `solution` is an array of step strings, each step is one logical move. Format inline math as `$...$` inside step strings (these go through pdflatex text mode and need wrapping). Use `\\displaystyle` if the step contains a long expression."""

NOTES_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subject": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "prose": {"type": "string"},
                    "math_blocks": {"type": "array", "items": {"type": "string"}},
                    "tikz_code": {"type": "string"},
                    "mermaid_code": {"type": "string"},
                    "graphviz_code": {"type": "string"},
                    "matplotlib_code": {"type": "string"},
                    "image": {"type": "string"},
                    "caption": {"type": "string"},
                    "examples": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "problem": {"type": "string"},
                                "solution": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["problem", "solution"],
                        },
                    },
                },
                "required": ["heading", "prose", "math_blocks", "tikz_code", "mermaid_code", "graphviz_code", "matplotlib_code", "examples"],
            },
        },
    },
    "required": ["title", "subject", "sections"],
}


def _normalize_notes_spec(spec: dict, query: str, subject: str | None) -> dict:
    spec = spec if isinstance(spec, dict) else {}
    sections = spec.get("sections", [])
    if not isinstance(sections, list):
        sections = []

    normalized_sections = []
    for idx, section in enumerate(sections, 1):
        if not isinstance(section, dict):
            continue
        examples = section.get("examples", [])
        if not isinstance(examples, list):
            examples = []
        normalized_examples = []
        for ex in examples:
            if not isinstance(ex, dict):
                continue
            solution = ex.get("solution", [])
            if isinstance(solution, str):
                solution = [solution]
            elif not isinstance(solution, list):
                solution = []
            normalized_examples.append({
                "problem": str(ex.get("problem", "")),
                "solution": [str(step) for step in solution],
            })

        math_blocks = section.get("math_blocks", [])
        if isinstance(math_blocks, str):
            math_blocks = [math_blocks]
        elif not isinstance(math_blocks, list):
            math_blocks = []

        normalized_sections.append({
            "heading": str(section.get("heading") or f"Section {idx}"),
            "prose": str(section.get("prose", "")),
            "math_blocks": [str(block) for block in math_blocks],
            "tikz_code": str(section.get("tikz_code", "")),
            "mermaid_code": str(section.get("mermaid_code", "")),
            "graphviz_code": str(section.get("graphviz_code", "")),
            "matplotlib_code": str(section.get("matplotlib_code", "")),
            "examples": normalized_examples,
            "image": str(section.get("image", "")),
            "caption": str(section.get("caption", "")),
        })

    if not normalized_sections:
        normalized_sections = [{
            "heading": "Insufficient Retrieved Context",
            "prose": "The retrieved database context was not enough to build reliable student notes for this topic. Please ingest or label more source material, then retry the request.",
            "math_blocks": [],
            "tikz_code": "",
            "mermaid_code": "",
            "graphviz_code": "",
            "matplotlib_code": "",
            "examples": [],
        }]

    return {
        "title": str(spec.get("title") or query),
        "subject": str(spec.get("subject") or subject or ""),
        "sections": normalized_sections,
    }


def _section_has_diagram(section: dict) -> bool:
    return any(
        (section.get(field) or "").strip()
        for field in ("tikz_code", "mermaid_code", "graphviz_code", "matplotlib_code", "image")
    )


# ---------------------------------------------------------------------------
# Pass A — outline
# ---------------------------------------------------------------------------
_OUTLINE_USER_TEMPLATE = """TOPIC: {query}

SUBJECT: {subject}

CONTEXT (retrieved academic material for this topic):
{context}

Produce the section outline now. Return ONLY the JSON object."""


def _generate_outline(query: str, subject: str | None, context: str, sections_min: int = 6) -> dict:
    from generate import call_llm_with_fallback, _parse_json

    system = (
        "You are an academic outline planner. Produce a JSON outline for a "
        "detailed, exam-ready student-notes document. Return ONLY JSON.\n"
        "\n"
        "Valid JSON example (copy this structure exactly, replace the values):\n"
        '{\n'
        '  "title": "Topic Title",\n'
        '  "subject": "Mathematics",\n'
        '  "sections": [\n'
        '    {\n'
        '      "heading": "Definition and Basic Properties",\n'
        '      "focus": "definition, properties, basic theorems",\n'
        '      "needs_diagram": true,\n'
        '      "diagram_kind": "tikz",\n'
        '      "diagram_focus": "geometric interpretation of the concept",\n'
        '      "example_count": 2\n'
        '    },\n'
        '    {\n'
        '      "heading": "Key Theorem",\n'
        '      "focus": "main theorem, proof outline",\n'
        '      "needs_diagram": false,\n'
        '      "diagram_kind": "mermaid",\n'
        '      "diagram_focus": "theorem proof flow",\n'
        '      "example_count": 1\n'
        '    }\n'
        '  ]\n'
        '}\n'
        f"Rules:\n"
        f"- Minimum {sections_min} sections. Cover the topic end-to-end: definitions, "
        f"key theorems/algorithms, derivations, worked examples, applications.\n"
        f"- At least 3 sections must have needs_diagram=true. Choose the diagram tool "
        f"best suited for each concept (tikz for geometry/coordinate graphs, mermaid for "
        f"flowcharts/processes, graphviz for trees/networks, matplotlib for plots, image "
        f"for surfacing a DB image).\n"
        f"- At least 3 sections must have example_count >= 1.\n"
        f"- OUTPUT VALID JSON. All property names and string values use DOUBLE QUOTES. "
        f"Escape double quotes inside strings as backslash-quote, backslashes as two backslashes, "
        f"newlines as backslash-n. Inside graphviz DOT strings, use single quotes for labels: label=\\'...\\'.\n"
        f"- Do NOT output markdown, backticks, or explanation. Start directly with the JSON object."
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _OUTLINE_USER_TEMPLATE.format(
            query=query, subject=subject or "general", context=context,
        )},
    ]

    for attempt in range(3):
        raw = call_llm_with_fallback(
            messages, role="notes", max_tokens=4096, thinking_disabled=True,
        )
        try:
            parsed = _parse_json(raw)
            sections = parsed.get("sections") if isinstance(parsed, dict) else None
            if not isinstance(sections, list) or not sections:
                raise ValueError("Empty or malformed sections list")
            return {
                "title": str(parsed.get("title") or query),
                "subject": str(parsed.get("subject") or subject or ""),
                "sections": sections,
            }
        except Exception as e:
            if attempt < 2:
                print(f"[notes] Outline JSON invalid (attempt {attempt+1}): {e}. Retrying with error feedback...")
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        f"The previous JSON could not be parsed. Error: {e}\n\n"
                        "Fix: Output ONLY valid JSON with DOUBLE QUOTES around all "
                        "property names and string values. No comments, no extra text, no backticks."
                    ),
                })
            else:
                raise ValueError(f"Outline generation failed after 3 attempts: {e}")
    raise RuntimeError("Unreachable: outline retry loop exhausted")


# ---------------------------------------------------------------------------
# Image inventory
# ---------------------------------------------------------------------------
def _build_image_inventory(query: str, subject: str | None, top_k: int = 12) -> list[dict]:
    """Pull image chunks for the topic; return them as a small list. Each entry
    has image_path, type, and caption the renderer can display."""
    from generate import retrieve

    try:
        chunks, _ = retrieve(query, k=top_k, subject=subject)
    except Exception as e:
        print(f"[notes] Image inventory retrieve failed ({e}); skipping.")
        return []
    images = [c for c in chunks if c.get("kind") == "image"]
    # Cap at 8 so the LLM prompt stays small but we still cover the topic.
    return images[:8]


def _format_image_inventory(images: list[dict]) -> str:
    if not images:
        return ""
    lines = ["AVAILABLE IMAGES (from the knowledge base — pick at most one per section):"]
    for i, m in enumerate(images, 1):
        lines.append(
            f"[img{i}] path={m.get('image') or m.get('image_path') or ''} "
            f"type={m.get('type', '')} caption={m.get('caption', '') or m.get('text', '')[:200]}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pass B — per-section drafts
# ---------------------------------------------------------------------------
_SECTION_USER_TEMPLATE = """TOPIC: {query}

SECTION HEADING: {heading}
SECTION FOCUS: {focus}

{diagram_directive}

{image_inventory}

CONTEXT (retrieved chunks for this section):
{context}

Return ONLY the JSON object for this single section."""

_SECTION_SYSTEM_PROMPT = """You are a senior university professor writing ONE section of an exam-ready textbook chapter.
You produce ONLY a single JSON object (no markdown, no commentary, no preamble). Start your response with `{{` and end with `}}`.

JSON shape for this single section:
{{
  "heading": str,                       // the section title from the outline
  "prose": str,                         // 3-5 full paragraphs of PLAIN ENGLISH ONLY (>=400 words). No math notation.
  "math_blocks": [str],                 // LaTeX display math expressions (no $$ or \\[ delimiters). ALL math lives here.
  "tikz_code": str,                     // optional FULL tikzpicture body (no \\begin{{tikzpicture}} wrapper), or ""
  "mermaid_code": str,                  // optional FULL mermaid diagram, or ""
  "graphviz_code": str,                 // optional FULL DOT graph, or ""
  "matplotlib_code": str,               // optional FULL matplotlib python, or ""
  "image": str,                         // optional path from AVAILABLE IMAGES, or ""
  "caption": str,                       // caption for whichever diagram/image you choose, or ""
  "examples": [ {{"problem": str, "solution": [str]}} ]
}}

HARD RULES (your output is rejected if any are violated):

1. PROSE IS PLAIN ENGLISH ONLY.
   The `prose` field is pdflatex text mode. ANY math token there renders as broken text.
   - DO NOT include `c^T x`, `x_B`, `B^{{-1}}`, `r_N`, `>=`, `<=`, Greek letters, subscripts, or superscripts in prose.
   - DO use words: "the transpose of c times x", "x sub B", "B inverse", "r sub N", "at least", "alpha", "sigma".
   - All formulas go into `math_blocks` with proper LaTeX: `\\min\\ c^T x`, `x_B`, `B^{{-1}}`, etc.

2. JSON SYNTAX — OUTPUT VALID JSON.
   - ALL property names and string values MUST use DOUBLE QUOTES (`"`).
   - Inside string values: escape double quotes as `\"`, backslashes as `\\`, newlines as `\n`.
   - Example mermaid label: `A["Start"]` → JSON: `"A[\\"Start\\"]"`.
   - Graphviz labels use single quotes in DOT: `label='Primal LP'` — no escaping needed in JSON.
   - Output one single JSON object, no ``` fences.

3. DIAGRAM COMPLETENESS — pick the tool that fits and emit a FULL, COMPILE-READY diagram.
   - `tikz_code`: body of a tikzpicture (no `\\begin{{tikzpicture}}`/`\\end{{tikzpicture}}` lines, no preamble, no `\\usetikzlibrary`). Math in TikZ uses `$...$`. Always include axes with `\\draw[->]`, labelled nodes for key points, and `\\fill`/`\\draw[thick]` for shaded regions or highlighted objects. Below is a CONCRETE EXAMPLE of what good TikZ looks like — match this style and depth:

     % 2D feasible region with shaded area, constraint line, optimal vertex marked
     \\draw[->] (-0.5,0) -- (6.5,0) node[right] {{$x_1$}};
     \\draw[->] (0,-0.5) -- (0,5.5) node[above] {{$x_2$}};
     \\draw[very thick, blue] (0,5) -- (5,0) node[midway, above left, blue] {{$x_1 + x_2 = 5$}};
     \\draw[very thick, blue] (0,4) -- (4,0) node[midway, below right, blue] {{$2x_1 + x_2 = 4$}};
     \\fill[orange!30] (0,0) -- (4,0) -- (2,2) -- (0,4) -- cycle;
     \\foreach \\p/\\l in {{(0,0)/O, (4,0)/A, (2,2)/B, (0,4)/C}}
       \\filldraw[red] \\p circle (2pt) node[above right, red] {{\\l}};
     \\node[below right, orange] at (1.5,1) {{Feasible}};

   - `mermaid_code`: FULL mermaid definition. ALL node labels MUST be double-quoted inside brackets: `A["Start"]`. NEVER use unquoted parentheses. HARD RULE: mermaid labels must be SHORT plain text (max 40 chars). No math, no underscores, no special characters — use words. Example:

     flowchart TD
        A["Start: B inverse b"] --> B{{"r_N >= 0?"}}
        B -->|Yes| C["Optimal"]
        B -->|No| D["Pick entering variable"]
        D --> E["Compute direction"]
        E --> F{{"Unbounded?"}}
        F -->|Yes| G["Unbounded"]
        F -->|No| H["Ratio test"]
        H --> A

   - `graphviz_code`: FULL DOT definition. Use `label='...'` with SINGLE quotes around label text — the renderer auto-converts to dot's required double quotes. Example:

     digraph simplex {{
       rankdir=LR;
       node [shape=box, style=rounded];
       BFS [label='Basic Feasible Solution'];
       NB [label='Neighbouring BFS'];
       Opt [label='Optimal BFS', shape=ellipse, color=green];
       BFS -> NB [label='pivot'];
       NB -> BFS [label='reverse pivot'];
       NB -> Opt [label='all r_N >= 0'];
     }}

   - `matplotlib_code`: FULL python that creates a figure. The renderer provides `matplotlib`, `matplotlib.pyplot as plt`, and `numpy as np` already. Do NOT call `plt.show()` or `plt.savefig(...)` — the renderer wraps savefig automatically. Example:

     x = np.linspace(-2, 4, 400)
     y1 = (5 - x)               # x_1 + x_2 = 5
     y2 = (4 - 2*x)             # 2x_1 + x_2 = 4
     plt.plot(x, y1, 'b-', linewidth=2, label='$x_1 + x_2 = 5$')
     plt.plot(x, y2, 'r-', linewidth=2, label='$2x_1 + x_2 = 4$')
     plt.fill_between(x, np.minimum(y1, y2), 0, where=(x>=0), alpha=0.3, color='orange')
     plt.axhline(0, color='black', linewidth=0.5)
     plt.axvline(0, color='black', linewidth=0.5)
     plt.xlim(-0.5, 4.5); plt.ylim(-0.5, 5.5)
     plt.xlabel('$x_1$'); plt.ylabel('$x_2$')
     plt.legend(loc='upper right')
     plt.title('Feasible Region for the LP')

4. CONTENT.
   - prose MUST be >= 3 full paragraphs (>=400 words). Definitions, intuitions, derivations, one concrete application.
   - {examples_directive}
   - {diagram_rule}
   - Ground facts in the CONTEXT.
   - Every theorem/algorithm gets a complete derivation in `math_blocks`.
   - For inline math inside `problem` or `solution` step strings, wrap with `$...$` (those go through text mode and need explicit delimiters)."""


def _generate_section_draft(
    query: str,
    subject: str | None,
    section: dict,
    section_context: str,
    image_inventory: list[dict],
) -> dict:
    from generate import call_llm_with_fallback, _parse_json

    needs_diagram = bool(section.get("needs_diagram"))
    diagram_kind = section.get("diagram_kind") or ("tikz" if needs_diagram else "none")
    example_count = max(int(section.get("example_count") or 0), 1)

    if needs_diagram:
        diagram_directive = (
            f"DIAGRAM REQUIRED: produce {diagram_kind} code for this section. "
            f"Focus: {section.get('diagram_focus') or section.get('focus', '')}."
        )
        diagram_rule = (
            f"You MUST fill exactly one of tikz_code, mermaid_code, graphviz_code, "
            f"matplotlib_code, or image with non-empty content. Use {diagram_kind} unless "
            f"the section clearly calls for a different tool."
        )
    else:
        diagram_directive = "DIAGRAM OPTIONAL: add a diagram only if it materially helps the section."
        diagram_rule = (
            "Diagrams are optional. If you add one, fill exactly one of tikz_code, "
            "mermaid_code, graphviz_code, matplotlib_code, or image."
        )

    examples_directive = (
        f"examples MUST contain exactly {example_count} item(s) with step-by-step "
        f"solution arrays (each step its own string)."
    )

    system = _SECTION_SYSTEM_PROMPT.format(
        examples_directive=examples_directive,
        diagram_rule=diagram_rule,
    )
    user = _SECTION_USER_TEMPLATE.format(
        query=query,
        heading=section.get("heading", ""),
        focus=section.get("focus", ""),
        diagram_directive=diagram_directive,
        image_inventory=_format_image_inventory(image_inventory),
        context=section_context,
    )

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    for attempt in range(2):
        raw = call_llm_with_fallback(
            messages,
            role="notes",
            max_tokens=32768,
            reasoning_effort="low",
        )
        try:
            return _parse_json(raw)
        except Exception as e:
            if attempt < 1:
                print(f"[notes] Section draft JSON invalid (attempt {attempt+1}): {e}. Retrying with error...")
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": f"The JSON was invalid: {e}. Output ONLY valid JSON matching the requested schema. Double quotes everywhere.",
                })
            else:
                raise
    raise RuntimeError("Unreachable: section draft retry loop exhausted")


# ---------------------------------------------------------------------------
# Pass B-supplement — diagram fill (when Pass B under-delivered diagrams)
# ---------------------------------------------------------------------------
_DIAGRAM_FILL_SCHEMA_BRIEF = """Return ONLY a JSON object:
{
  "diagrams": [
    {"heading": str, "kind": "tikz"|"mermaid"|"graphviz"|"matplotlib", "code": str, "caption": str}
  ]
}

Rules:
- `code` is the FULL body of the diagram (no `\\begin{tikzpicture}`/`\\end{tikzpicture}` wrapper for tikz; no `plt.show()` for matplotlib).
- OUTPUT VALID JSON: ALL property names and string values MUST use DOUBLE QUOTES (`"`). Escape `"` as `\"`, `\\` as `\\\\`, newlines as `\\n`.
- Inside `code` strings:
  - Mermaid labels: `A["Start"]` → JSON: `"A[\\"Start\\"]"`
  - Graphviz labels: `label='Primal LP'` (single quotes in DOT, no JSON escaping needed).
- TikZ must include axes, labels, and shading/arrows as the concept needs. Use `$...$` for inline math.
- Output JSON only. No markdown fences, no commentary."""


def _backfill_diagrams(sections: list[dict], target_min: int, context: str, query: str) -> None:
    """If fewer than target_min sections have a diagram, ask the LLM to add
    TikZ/mermaid/matplotlib code for the bare sections. Mutates sections in place."""
    from generate import call_llm_with_fallback, _parse_json

    bare = [s for s in sections if not _section_has_diagram(s)]
    if not bare or len(sections) - len(bare) >= target_min:
        return

    system = (
        "You are a diagram-generation specialist. The following student-notes sections "
        "have no diagram yet. Produce one diagram per section, choosing the tool best "
        "suited for the concept (tikz for geometry/coordinate plots, mermaid for "
        "flowcharts/processes, graphviz for trees, matplotlib for equation plots). "
        + _DIAGRAM_FILL_SCHEMA_BRIEF
    )
    user = (
        f"TOPIC: {query}\n\n"
        f"BARE SECTIONS (need diagrams):\n"
        f"{json.dumps([{'heading': s.get('heading', ''), 'focus': s.get('prose', '')[:200]} for s in bare], indent=2)}\n\n"
        f"CONTEXT (snippets to ground the diagrams):\n{context[:6000]}"
    )
    raw = call_llm_with_fallback(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        role="notes",
        max_tokens=8192,
        reasoning_effort="high",
    )
    try:
        parsed = _parse_json(raw)
    except Exception as e:
        print(f"[notes] Diagram fill JSON failed: {e}")
        return
    diagrams = parsed.get("diagrams") if isinstance(parsed, dict) else None
    if not isinstance(diagrams, list):
        return
    by_heading = {d.get("heading", "").strip().lower(): d for d in diagrams if isinstance(d, dict)}
    consumed = []
    for section in bare:
        diag = by_heading.get(section["heading"].strip().lower())
        if diag is None and diagrams:
            for d in diagrams:
                if d not in consumed:
                    diag = d
                    consumed.append(d)
                    break
        if not isinstance(diag, dict):
            continue
        kind = (diag.get("kind") or "").strip().lower()
        code = (diag.get("code") or "").strip()
        if not code:
            continue
        field = {
            "tikz": "tikz_code",
            "mermaid": "mermaid_code",
            "graphviz": "graphviz_code",
            "matplotlib": "matplotlib_code",
        }.get(kind)
        if field:
            section[field] = code
        if diag.get("caption") and not section.get("caption"):
            section["caption"] = diag["caption"]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def generate_notes_spec(query: str, subject: str | None = None, top_k: int = 8) -> dict:
    """Three-pass generation. `top_k` is the per-section retrieval depth."""
    from generate import (
        retrieve, build_context, call_llm_with_fallback, _parse_json,
        TIKZ_GENERATION_SYSTEM_PROMPT,
    )

    # Topic-level retrieval for outline + image inventory.
    topic_chunks, _ = retrieve(query, k=max(top_k, 8), subject=subject)
    topic_context = build_context(topic_chunks)
    image_inventory = _build_image_inventory(query, subject, top_k=12)

    # Pass A — outline.
    print(f"[notes] Pass A: outline (top_k={top_k}, images={len(image_inventory)})...")
    try:
        outline = _generate_outline(query, subject, topic_context)
    except Exception as e:
        print(f"[notes] Outline pass failed ({e}); falling back to single-shot.")
        return _generate_notes_spec_single_shot(query, subject, top_k)

    outline_sections = outline["sections"]
    title = outline["title"]
    title_subject = outline["subject"] or subject or ""

    # Pass B — per-section drafts. Retrieve per-section context sequentially (BGE-M3
    # is GPU-bound and re-entrant calls serialize anyway), then issue LLM calls in
    # parallel via threads — each call is I/O-bound on the DeepSeek socket.
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Pre-build per-section context (sequential, embedded model loaded once).
    section_contexts: list[tuple[dict, str]] = []
    for idx, section in enumerate(outline_sections, 1):
        heading = section.get("heading") or f"Section {idx}"
        focus = section.get("focus") or heading
        try:
            sec_chunks, _ = retrieve(focus, k=top_k, subject=subject)
            sec_context = build_context(sec_chunks)
        except Exception as e:
            print(f"[notes] Pass B retrieve failed for '{heading}': {e}; using topic context.")
            sec_context = topic_context
        section_contexts.append((section, sec_context))

    def _draft_one(idx_and_section_ctx: tuple[int, dict, str]) -> tuple[int, dict | None]:
        idx, section, sec_context = idx_and_section_ctx
        heading = section.get("heading") or f"Section {idx}"
        print(f"[notes] Pass B {idx}/{len(outline_sections)}: {heading[:60]}...")
        try:
            draft = _generate_section_draft(query, subject, section, sec_context, image_inventory)
        except Exception as e:
            print(f"[notes] Pass B draft failed for '{heading}': {e}; skipping section.")
            return idx, None
        if not isinstance(draft, dict):
            return idx, None
        if not (draft.get("prose") or "").strip():
            try:
                draft = _generate_section_draft(query, subject, section, topic_context, image_inventory)
            except Exception:
                pass
        if not draft.get("heading"):
            draft["heading"] = heading
        return idx, draft

    parallel_inputs = [
        (idx, section, ctx) for idx, (section, ctx) in enumerate(section_contexts, 1)
    ]
    indexed_drafts: dict[int, dict] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(parallel_inputs))) as ex:
        for idx, draft in ex.map(_draft_one, parallel_inputs):
            if draft is not None:
                indexed_drafts[idx] = draft
    # Reassemble in original outline order so the rendered PDF reads top-to-bottom.
    draft_sections = [indexed_drafts[i] for i in sorted(indexed_drafts)]

    if not draft_sections:
        print("[notes] All Pass B drafts failed; falling back to single-shot.")
        return _generate_notes_spec_single_shot(query, subject, top_k)

    # Pass C — assemble.
    spec = {
        "title": title,
        "subject": title_subject,
        "sections": draft_sections,
    }
    spec = _normalize_notes_spec(spec, query, subject)

    # Diagram quota enforcer.
    target_min = max(3, len(spec["sections"]) // 2)
    have_diagrams = sum(1 for s in spec["sections"] if _section_has_diagram(s))
    if have_diagrams < target_min:
        print(f"[notes] Diagram quota short ({have_diagrams}/{target_min}); running fill pass.")
        _backfill_diagrams(spec["sections"], target_min, topic_context, query)

    # Existing TikZ second-pass: regenerate full TikZ bodies for any section that
    # has a non-empty stub from Pass B / fill. This produces compilable code.
    tikz_sections = [s for s in spec["sections"] if (s.get("tikz_code") or "").strip()]
    if tikz_sections:
        tikz_requests = [{"title": s["heading"], "focus": s["tikz_code"]} for s in tikz_sections]
        tikz_messages = [
            {"role": "system", "content": TIKZ_GENERATION_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Generate TikZ diagrams for the following sections.\n"
                f"{json.dumps(tikz_requests, indent=2)}"
            )},
        ]
        try:
            tikz_raw = call_llm_with_fallback(
                tikz_messages, role="tikz", max_tokens=16384, reasoning_effort="low",
            )
            diagrams = _parse_json(tikz_raw).get("diagrams", [])
            diag_by_title = {d.get("title", "").strip().lower(): d for d in diagrams}
            for section in tikz_sections:
                key = section["heading"].strip().lower()
                diag = diag_by_title.get(key)
                if diag is None and diagrams:
                    diag = diagrams.pop(0)
                if diag:
                    section["tikz_code"] = diag.get("code", section["tikz_code"])
        except Exception as e:
            print(f"[notes] TikZ generation failed ({e}); keeping LLM-provided stubs.")

    return spec


def _generate_notes_spec_single_shot(query: str, subject: str | None, top_k: int) -> dict:
    """Original one-shot generation path. Used as a fallback when the multi-pass
    pipeline cannot produce a usable outline or any section drafts."""
    from generate import retrieve, build_context, call_llm_with_fallback, _parse_json

    chunks, _ = retrieve(query, k=top_k, subject=subject)
    context_str = build_context(chunks)
    addon = NOTES_SUBJECT_ADDONS.get(subject or "", "")

    messages = [
        {"role": "system", "content": NOTES_SYSTEM_PROMPT + ("\n\n" + addon if addon else "")},
        {"role": "user", "content": f"TOPIC: {query}\n\nCONTEXT:\n{context_str}"},
    ]
    raw = None
    try:
        raw = call_llm_with_fallback(
            messages,
            role="notes",
            max_tokens=16384,
            response_schema=NOTES_RESPONSE_SCHEMA,
            reasoning_effort="high",
        )
        parsed = _parse_json(raw)
    except Exception as e:
        if raw is None:
            if top_k > 2:
                print(f"[notes] Single-shot fallback failed ({e}). Retrying with reduced context (top_k={top_k - 2})...")
                return _generate_notes_spec_single_shot(query, subject, top_k - 2)
            raise e
        print(f"[notes] JSON parsing failed: {e}. Attempting repair...")
        try:
            repair_messages = [
                {"role": "system", "content": (
                    "Repair the following malformed notes JSON into one valid JSON object "
                    "matching the requested notes schema. Output JSON only."
                )},
                {"role": "user", "content": raw[:20000]},
            ]
            repaired = call_llm_with_fallback(
                repair_messages,
                role="notes",
                max_tokens=16384,
                response_schema=NOTES_RESPONSE_SCHEMA,
                reasoning_effort="high",
            )
            parsed = _parse_json(repaired)
        except Exception as repair_err:
            print(f"[notes] Repair pass failed: {repair_err}.")
            if top_k > 2:
                print(f"[notes] Retrying with reduced context (top_k={top_k - 2})...")
                return _generate_notes_spec_single_shot(query, subject, top_k - 2)
            raise repair_err
    return _normalize_notes_spec(parsed, query, subject)


def run(query: str, subject: str | None = None) -> Path:
    from generate import _slug
    from render_notes import render
    import cost_logger

    spec = generate_notes_spec(query, subject=subject)
    slug = _slug(query)
    out_dir = OUTPUT / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "notes_spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    pdf_path = out_dir / "student_notes.pdf"
    render(spec, pdf_path, mode="notes")
    
    # Write cost log for this run
    try:
        cost_logger.write_log("notes", query, subject, ok=True)
    except Exception:
        pass
    
    return pdf_path
