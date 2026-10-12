"""Phase 5 — RAG generation.

Retrieve the top-k chunks (text + image) for a topic query from Qdrant, hand
them to an OpenRouter model with the slide-spec contract, validate the returned
JSON against the schema, then render a .pptx. The spec is saved alongside the
deck for audit.

TWO-STAGE RETRIEVAL:
  Stage 1: Retrieve top-20 candidates broadly (no filter).
  Stage 2: Filter to chunks scoring above SCORE_THRESHOLD, cap at TOP_K.
  Optional `subject` param scopes retrieval to a single subject area using
  the Qdrant payload index — O(log n) even at millions of chunks.
"""
import json
import re
from pathlib import Path

from jsonschema import validate, ValidationError

from config import ROOT, OUTPUT

SCHEMA = json.loads((ROOT / "templates" / "slide_spec.schema.json").read_text())
TOP_K = 8
BROAD_K = 24          # Stage-1 retrieval count (always >= TOP_K)
SCORE_THRESHOLD = 0.30  # Minimum cosine similarity to include a chunk

# ---------------------------------------------------------------------------
# Subject-specific guidance injected into the system prompt
# ---------------------------------------------------------------------------
SUBJECT_ADDONS = {
    "mathematics": """
SUBJECT: MATHEMATICS
Use theorem/definition/example slide types heavily. Structure:
- Every major theorem MUST have a 'theorem' slide (statement + proof sketch steps).
- Every new concept MUST have a 'definition' slide with precise notation.
- Include 2+ 'example' slides with step-by-step worked solutions.
- Use 'tikz' slides for: geometric constructions, number lines, set diagrams,
  function graphs, proof trees, commutative diagrams.
- Use 'derivation' slides for multi-step algebraic manipulation.
- Use 'matplotlib' slides for: plotting functions over a domain, comparing curves,
  graphing sequences/series.
Aim for 12-16 slides total. Do not pad with generic bullet slides.
- TikZ diagram rules for MATHEMATICS:
  * Sequence/AP node diagrams: label each node with term index (n=1,2,...) BELOW the node and term value inside; use separate colors per sequence type with a legend.
  * AP coordinate plots: draw axes with \\draw[-{Stealth}] and add tick marks every unit; include grid lines (\\draw[gray!30,step=1] grid); keep all points within y=[0.5, 5.5] to avoid canvas clipping.
  * Sierpinski triangle: center the outer triangle at (5, 3.5) in a 10x7 canvas with 4cm base; label each stage with \\node text showing "Stage 0", "Stage 1", etc.; no element within 0.5cm of canvas edge.
  * Number lines: mark integers with ticks, label with values, use arrows on both ends.
""",
    "physics": """
SUBJECT: PHYSICS
Structure:
- Include 'derivation' slides showing step-by-step dimensional analysis and math.
- Include 'definition' slides for each new physical quantity (symbol, SI unit, formula).
- Use 'tikz' slides for: free body diagrams, circuit schematics, ray diagrams,
  vector addition diagrams, field line sketches, wave interference patterns.
- Use 'matplotlib' slides for: function plots (displacement vs time, force vs extension,
  wave amplitude vs position, velocity vs time).
- Every law/principle gets a 'theorem' slide with statement + conditions of validity.
Aim for 12-16 slides total.
- TikZ diagram rules for PHYSICS:
  * Free body diagrams: label every force arrow with symbol AND magnitude formula (e.g. "mg", "N", "f"); use colored arrows (brandTeal for normal, accentOrange for friction, waterBlue for applied); add coordinate axes with labels.
  * Circuit diagrams: label each component (R1, C1, L1) with \\node below; mark voltage/current direction with arrows; include a legend box.
  * Wave diagrams: draw at least 2 full cycles; label amplitude, wavelength, and equilibrium line with tick marks; use brandTeal for wave, earthBrown for equilibrium.
  * Vector diagrams: show component decomposition with dashed lines; label angle with \\node and formula; every vector tip gets an arrowhead.
  * No element within 0.5cm of canvas edge (canvas 10cm x 7cm).
""",
    "chemistry": """
SUBJECT: CHEMISTRY
Structure:
- Include 'derivation' slides for equilibrium constants, rate laws, thermodynamic cycles.
- Use 'tikz' slides for: reaction energy profile diagrams, electron dot structures,
  molecular orbital overlap diagrams, titration curves (schematic), orbital shapes.
- Use 'definition' slides for nomenclature, functional groups, and key terms.
- Include 'example' slides with worked stoichiometry / equilibrium problems.
Aim for 12-16 slides total.
- TikZ diagram rules for CHEMISTRY:
  * Energy profile (reaction coordinate): draw a smooth curve with \\draw[smooth]; label reactants, products, transition state with \\node; annotate ΔH and Ea with bidirectional arrows and text; x-axis="Reaction Coordinate", y-axis="Energy (kJ/mol)" with grid.
  * Electron dot / Lewis structures: draw each atom as a labeled circle \\node; draw bond lines between atoms; surround lone pairs with small dots using \\fill circle (1.5pt).
  * Orbital shapes: use \\fill[opacity=0.4] for orbital lobes; label each lobe with sign (+/-) and orbital name; show nodal plane with dashed line.
  * No element within 0.5cm of canvas edge (canvas 10cm x 7cm).
""",
    "biology": """
SUBJECT: BIOLOGY
Structure:
- Use 'tikz' slides for: cell organelle diagrams, process flowcharts
  (DNA replication, protein synthesis, photosynthesis), food web diagrams,
  ecosystem energy pyramids, life cycle flowcharts.
- Include 'definition' slides for key terms with etymology where helpful.
- Include 'example' slides showing experimental observations or case studies.
Aim for 12-16 slides total.
- TikZ diagram rules for BIOLOGY:
  * Process flowcharts (DNA replication, photosynthesis): use rounded rectangle \\node[draw,rounded corners] for each step; connect with -{Stealth} arrows; label each arrow with the enzyme or molecule involved; use waterBlue for input steps, forestGreen for output steps, earthBrown for enzyme nodes.
  * Cell diagrams: draw each organelle as a distinct shape with a \\node label outside pointing inward with a dashed leader line; color code organelle types.
  * Food webs: arrange producers at bottom, consumers ascending; use green for autotrophs, orange for heterotrophs; label each species \\node; arrows point FROM prey TO predator.
  * Energy pyramids: draw stacked trapezoids shrinking upward; label trophic level name + energy value on each level.
  * No element within 0.5cm of canvas edge (canvas 10cm x 7cm).
""",
    "computer_science": """
SUBJECT: COMPUTER SCIENCE
Structure:
- Use 'mermaid' slides for: algorithm flowcharts, data structure diagrams
  (trees, linked lists, graphs, hash tables), automata/state machine diagrams,
  memory layout diagrams, network topology.
- Include 'derivation' slides for time complexity analysis (recurrences, Big-O).
- Include 'example' slides with concrete input/output traces of algorithms.
- Include pseudocode in bullet slides using \\texttt{} notation.
Aim for 12-16 slides total.
- Mermaid diagram rules for COMPUTER SCIENCE:
  * Output ONLY a valid Mermaid diagram code block. Do NOT include ```mermaid or ``` in the 'code' field.
  * Use TD (top-down) flowcharts for algorithms, graph TD/LR for tree/graph structures, or stateDiagram-v2/sequenceDiagram as appropriate.
  * Wrap node labels in double quotes (e.g. A["Label Text"]) to avoid syntax errors with special characters.
  * Keep the diagram simple (max 10-12 nodes).
""",
    "geography": """
SUBJECT: GEOGRAPHY / EARTH SCIENCE
Structure:
- Use 'tikz' slides for: hydrological cycle, rock cycle, climate zone diagrams,
  dam/irrigation system cross-sections, soil layer profiles, wind pattern schematics.
- Include 'definition' slides for key geographical terms.
- Include 'example' slides with case studies (specific regions, rivers, or events).
Aim for 12-16 slides total.
- TikZ diagram rules for GEOGRAPHY:
  * Hydrological cycle: draw arrows for evaporation, condensation, precipitation, runoff, infiltration; label each process with \\node text; use waterBlue for water, earthBrown for ground/soil, lightBg sky fill.
  * Cross-section diagrams (dam, soil profile): draw layered horizontal bands with \\fill; label each layer on the right side with a leader line; include a scale bar.
  * Climate zone diagrams: divide a schematic globe into colored bands; label each zone (Tropical, Temperate, Polar) with \\node; add sun position arrows.
  * No element within 0.5cm of canvas edge (canvas 10cm x 7cm).
""",
    "history": """
SUBJECT: HISTORY
Structure:
- Use 'tikz' slides for: timelines, cause-and-effect flowcharts, empire maps
  (schematic, not geographic), political hierarchy diagrams.
- Include 'example' slides with specific dates, events, and primary source excerpts.
Aim for 12-16 slides total.
- TikZ diagram rules for HISTORY:
  * Timelines: draw a horizontal line with tick marks; label each event ABOVE the line with date and below with event name; alternate colors for adjacent events (brandTeal / accentOrange); use arrows at both ends.
  * Cause-and-effect flowcharts: box each cause/effect with \\node[draw,rounded corners]; connect with labeled arrows; color code: earthBrown for causes, waterBlue for effects, forestGreen for outcomes.
  * Hierarchy diagrams: place top of hierarchy at top; use \\node[draw,fill] rectangles; connect parent to children with lines; label each box with role/title.
  * No element within 0.5cm of canvas edge (canvas 10cm x 7cm).
"""
}

def _web_supplement(query: str) -> str:
    """Fetch a short Wikipedia summary for the query (stdlib only, no new deps)."""
    # ponytail: disabled web research completely as requested
    return ""


from functools import lru_cache

RERANK_MODEL = "BAAI/bge-reranker-v2-m3"  # multilingual, pairs with BGE-M3


@lru_cache(maxsize=1)
def _reranker():
    from sentence_transformers import CrossEncoder  # FlagEmbedding pins transformers 4.x, EmbeddingGemma 2 needs 5.x
    import torch
    print(f"[generate] Loading Cross-Encoder '{RERANK_MODEL}'...")
    cuda = torch.cuda.is_available()
    return CrossEncoder(RERANK_MODEL, device="cuda" if cuda else "cpu",
                        model_kwargs={"torch_dtype": torch.float16 if cuda else torch.float32})


def rerank_scores(query: str, texts: list[str]) -> list[float]:
    """Sigmoid cross-encoder scores in [0, 1]. The model sits in CPU memory between calls and visits the GPU only
    while scoring, so the 6 GB card is free for the embedder, Whisper and ingest jobs the rest of the time.
    RERANK_KEEP_ON_GPU=1 keeps it resident (saves ~0.2 s per query on a bigger card)."""
    import os
    import torch

    model = _reranker()
    cuda = torch.cuda.is_available()
    offload = cuda and os.getenv("RERANK_KEEP_ON_GPU") != "1"
    if cuda:
        model.model.to("cuda")
    try:
        return model.predict([[query, t] for t in texts], activation_fn=torch.nn.Sigmoid()).tolist()
    finally:
        if offload:
            model.model.to("cpu")
            torch.cuda.empty_cache()


# Scores are sigmoid-normalized to [0,1] (compute_score(normalize=True)), so
# thresholds are interpretable probabilities, not raw logits, and transfer
# across queries. Paired with a keep-top-k safety net so a grading miss can
# never drop the answer chunk entirely.
RERANK_KEEP = 0.30     # keep reranked chunks scoring >= this
RERANK_REFUSE = 0.04   # refuse to generate only if even the top chunk is below this
RERANK_LOWCONF = 0.20  # avg below this => low confidence (KG prerequisite fallback)
# Back-compat alias (eval/tests may import it); now a probability floor.
RERANK_THRESHOLD = RERANK_REFUSE


def _get_registry_hash() -> str:
    from config import REGISTRY
    import hashlib
    if not REGISTRY.exists():
        return ""
    try:
        return hashlib.sha256(REGISTRY.read_bytes()).hexdigest()
    except Exception:
        return ""


def _load_cache() -> tuple[str, dict]:
    from config import METADATA
    cache_file = METADATA / "spec_cache.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            return data.get("registry_hash", ""), data.get("cache", {})
        except Exception:
            pass
    return "", {}


def _save_cache(registry_hash: str, cache_dict: dict) -> None:
    from config import METADATA
    cache_file = METADATA / "spec_cache.json"
    try:
        METADATA.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps({
            "registry_hash": registry_hash,
            "cache": cache_dict
        }, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[generate] Warning: failed to save cache: {e}")


def _build_refusal_spec(query: str) -> dict:
    return {
        "title": "Insufficient Information",
        "subtitle": f"No highly relevant content found for query '{query}'",
        "query": query,
        # Signals to run() that no indexed material backs this deck — the
        # McKinsey PPTX should be authored from the LLM's own knowledge
        # instead of inheriting this placeholder as its "context".
        "ungrounded": True,
        "slides": [
            {
                "type": "section",
                "number": 1,
                "title": "Topic Not Found in Context Chunks"
            },
            {
                "type": "bullets",
                "title": "Search Status",
                "bullets": [
                    "The database search did not return matches exceeding the required confidence threshold.",
                    "Please refine your query or search for a topic covered in the ingested textbooks."
                ],
                "source": {"doc": "System", "loc": "N/A"}
            }
        ]
    }


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:50] or "deck"


def retrieve(
    query: str,
    k: int = TOP_K,
    subject: str | None = None,
    learner_profile: dict | None = None,
    tutor: bool = False,
) -> tuple[list[dict], bool]:
    """Two-stage hybrid retrieval:
      - Reranks text chunks via Cross-Encoder (BAAI/bge-reranker-base).
      - Preserves up to 2 high-scoring image chunks to ensure visual richness.

    tutor=True keeps the RRF order (no has_math re-sort) and skips the last-word prerequisite
    injection; tutor/retrieve.py adds prerequisites from tagged concepts instead.
    """
    import store
    from embeddings import encode_hybrid

    dense_vecs, sparse_vecs = encode_hybrid(query, is_query=True)
    qvec = dense_vecs[0]
    qsparse = sparse_vecs[0] if sparse_vecs else None

    # Stage 1: retrieve broadly via hybrid RRF (dense + sparse)
    broad = store.query(
        store.client(), qvec, k=max(BROAD_K, k * 3),
        subject=subject, sparse_vector=qsparse, math_boost=not tutor,
    )

    # Fallback: if subject-specific retrieval yields very few results, do unfiltered search
    if len(broad) < 3 and subject:
        print(f"[generate] Subject-specific search for '{subject}' yielded only {len(broad)} chunks. Falling back to unfiltered database query.")
        broad = store.query(
            store.client(), qvec, k=max(BROAD_K, k * 3),
            subject=None, sparse_vector=qsparse, math_boost=not tutor,
        )

    # Stage 1.5: page-image leg (ColSmol + EmbeddingGemma image vectors) adds the units on the best-matching pages
    from ingest import page_embed
    if page_embed.ENABLED:
        try:
            seen = {c.get("unit_id") for c in broad}
            broad += [c for c in page_embed.page_chunks(query, qvec) if c.get("unit_id") not in seen]
        except Exception as e:
            print(f"[generate] Warning: page retrieval failed ({e}).")

    # Stage 2: CLAF rerank if learner profile provided
    if learner_profile:
        from claf import rerank
        broad = rerank(broad, learner_profile)

    reranked = False
    images = [c for c in broad if c.get("kind") == "image"]
    texts = [c for c in broad if c.get("kind") != "image"]  # text and video transcript chunks

    # Stage 2.5: Cross-Encoder reranking for text chunks
    if texts:
        try:
            scores = rerank_scores(query, [c.get("text", "") for c in texts])  # sigmoid -> [0,1]
            for c, score in zip(texts, scores):
                c["score"] = float(score)
            texts.sort(key=lambda x: x["score"], reverse=True)
            print(f"[generate] Cross-Encoder reranked {len(texts)} texts. Top score: {texts[0]['score']:.4f}")
            reranked = True
        except Exception as e:
            print(f"[generate] Warning: Cross-Encoder rerank failed ({e}), falling back to retrieval score.")
            texts.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Stage 3: Filter and combine texts and images
    # Subject-aware thresholds for math
    if subject == "mathematics":
        threshold = 0.15 if reranked else 0.15
        rerank_keep = 0.15
        rerank_refuse = 0.01
        broad_k = 50
    else:
        threshold = RERANK_KEEP if reranked else SCORE_THRESHOLD
        rerank_keep = RERANK_KEEP
        rerank_refuse = RERANK_REFUSE
        broad_k = BROAD_K
    
    filtered_texts = [c for c in texts if c.get("score", 0) >= threshold]

    # Keep up to 2 high-scoring image chunks (stage-1 similarity score >= 0.08)
    relevant_images = [img for img in images if img.get("score", 0) >= 0.08]
    top_images = relevant_images[:2]

    # Fill remaining slots with text chunks. Safety net: if the threshold left
    # too few, top up from the ranked list so we never starve the writer of
    # context (a grading miss must not drop the answer chunk).
    num_texts_needed = k - len(top_images)
    top_texts = filtered_texts[:num_texts_needed]
    if len(top_texts) < num_texts_needed:
        for c in texts:
            if c not in top_texts:
                top_texts.append(c)
            if len(top_texts) >= num_texts_needed:
                break
    
    top = top_texts + top_images

    # Stage 4: knowledge graph prerequisite injection (always-on, min_weight=2 default prunes noise)
    try:
        from knowledge_graph import build_graph, get_prerequisites
        G = build_graph()
        if G.number_of_nodes() > 0 and not tutor:
            # Longest graph concept named in the query (was the query's last word, which rarely is a concept).
            q = f" {re.sub(r'[^a-z0-9]+', ' ', query.lower())} "
            top_concept = max((n for n in G if f" {n} " in q), key=len, default=None)
            prereqs = get_prerequisites(G, top_concept, depth=2) if top_concept else []
            if prereqs:
                c = store.client()
                for prereq in prereqs[:3]:
                    # Retrieve chunks ABOUT the prerequisite concept, not a
                    # duplicate of the original query (the old bug).
                    p_dense, p_sparse = encode_hybrid(prereq, is_query=True)
                    prereq_chunks = store.query(
                        c, p_dense[0], k=2,
                        sparse_vector=p_sparse[0] if p_sparse else None,
                    )
                    for chunk in prereq_chunks:
                        chunk["_is_prerequisite"] = True
                        chunk["_prereq_concept"] = prereq
                    top = prereq_chunks + top
    except Exception as e:
        print(f"[generate] Warning: prerequisite lookup failed ({e}).")

    return top[:k], reranked


def build_context(chunks: list[dict]) -> str:
    lines = []
    for i, m in enumerate(chunks, 1):
        if m.get("kind") == "image":
            lines.append(
                f"[{i}] IMAGE image_path={m.get('image')} type={m.get('type')} "
                f"caption={m.get('caption')} | {m.get('text', '')}"
            )
        else:
            # parent_text = full section body (richer LLM context); fall back to small chunk
            body = m.get("parent_text") or m.get("text", "")
            lines.append(
                f"[{i}] TEXT doc={m.get('doc')} heading={m.get('heading')} | {body}"
            )
    return "\n\n".join(lines)


# Real LaTeX macro names that start with each of these letters — used to tell
# "\node", "\right", etc. apart from an ordinary "\n"/"\r" the LLM meant as a
# newline. The old heuristic ("any letter follows -> assume a macro") false-
# positived on every prose newline before a capitalized sentence (e.g.
# "...object.\nThis law..."), leaving a literal backslash+n in the string that
# tex_escape() then rendered as the stray "\{}n" artifact seen in output.
_LATEX_MACROS_BY_LETTER = {
    "n": {"nabla", "ne", "neq", "node", "newline", "newpage", "noindent",
          "nonumber", "nolimits", "notag", "nicefrac", "notin"},
    "r": {"right", "rangle", "rceil", "rfloor", "rho", "rightarrow",
          "renewcommand", "requirepackage"},
    "t": {"theta", "times", "to", "tau", "text", "textbf", "textit",
          "textcolor", "textwidth", "tikzpicture", "tfrac"},
    "f": {"frac", "forall", "phi"},  # phi rarely typo'd here, kept defensive
    "b": {"beta", "begin", "big", "bigg", "bfseries", "boldsymbol"},
}


def _next_word(text: str, start: int) -> str:
    """Collect the run of ASCII letters starting at `start` (used to test
    whether a backslash-letter sequence spells a real LaTeX macro name)."""
    j = start
    while j < len(text) and text[j].isalpha():
        j += 1
    return text[start:j]


def fix_json_escapes(raw_json: str) -> str:
    """Scan raw JSON text, find string literals, and escape unescaped backslashes and control chars.

    Preserves:
      - \\" (escaped quote)
      - \\\\ (escaped backslash)
      - \\uXXXX (valid unicode escapes)
    Escapes all other backslashes and converts literal control characters to safe escapes.
    """
    in_string = False
    result = []
    i = 0
    n = len(raw_json)
    while i < n:
        char = raw_json[i]
        if not in_string:
            if char == '"':
                in_string = True
            result.append(char)
            i += 1
        else:
            # We are inside a JSON string
            if char == '"':
                in_string = False
                result.append(char)
                i += 1
            elif char == '\\':
                # Look ahead
                if i + 1 < n:
                    next_char = raw_json[i+1]
                    if next_char == '"':
                        result.append('\\"')
                        i += 2
                    elif next_char == '\\':
                        result.append('\\\\')
                        i += 2
                    elif next_char == '/':
                        result.append('\\/')
                        i += 2
                    elif next_char in ('n', 'r', 't', 'f', 'b'):
                        # \n/\r/\t/\f/\b are only real LaTeX macros (\node,
                        # \right, \theta, \frac, \begin, ...) when the word
                        # that follows is an actual macro name — not just
                        # "some letter comes next", which prose triggers on
                        # every "\nThis"/"\nA"/... sentence-starting newline.
                        rest = _next_word(raw_json, i + 2)
                        candidate = next_char + rest
                        if rest and candidate in _LATEX_MACROS_BY_LETTER.get(next_char, ()):
                            result.append('\\\\')
                            i += 1
                        else:
                            result.append(f'\\{next_char}')
                            i += 2
                    elif next_char == 'u' and i + 5 < n and all(c in "0123456789abcdefABCDEF" for c in raw_json[i+2:i+6]):
                        result.append(raw_json[i:i+6])
                        i += 6
                    else:
                        result.append('\\\\')
                        i += 1
                else:
                    result.append('\\\\')
                    i += 1
            elif char == '\n':
                result.append('\\n')
                i += 1
            elif char == '\r':
                result.append('\\r')
                i += 1
            elif char == '\t':
                result.append('\\t')
                i += 1
            else:
                result.append(char)
                i += 1
    return "".join(result)


def _find_json_blocks(text: str) -> list[str]:
    """Return all top-level {…} blocks using brace matching.

    Unlike `re.search(r"{.*}", text, re.DOTALL)` (which greedily matches
    from the first '{' to the LAST '}', swallowing everything between — often
    non-JSON thinking output), this function isolates each outermost block so
    callers can try each one independently.
    """
    blocks: list[str] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                blocks.append(text[start:i + 1])
                start = -1
    return blocks


def _parse_json(text: str) -> dict:
    if not text:
        raise ValueError(
            "Model returned an empty or null response — cannot parse JSON. "
            "This usually means the model hit a rate limit or content filter."
        )
    orig_text = text
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1].removeprefix("json").strip()

    escaped_text = fix_json_escapes(text)

    # Brace-balanced extraction: try each top-level {…} block individually.
    # This is robust against thinking-output noise that contains stray {…}
    # fragments (DeepSeek V4 Pro with reasoning enabled) — the real JSON
    # block is tried independently and parsed correctly.
    for block in _find_json_blocks(escaped_text):
        try:
            return json.loads(block)
        except Exception:
            continue

    # Last resort: treat the entire escaped text as the JSON body.
    try:
        return json.loads(escaped_text)
    except Exception as e:
        debug_path = OUTPUT / "failed_json.txt"
        debug_path.write_text(
            f"--- ERROR ---\n{e}\n\n--- ORIGINAL ---\n{orig_text}\n\n--- ESCAPED ---\n{escaped_text}\n",
            encoding="utf-8"
        )
        raise e



DECK_PLANNING_SYSTEM_PROMPT = None  # kept for external imports; deterministic planner used instead

CONTENT_GENERATION_SYSTEM_PROMPT = """You are a Senior Academic Content Writer producing EXAM-READY lecture notes.
Write detailed, dense, factually grounded content for assigned slides. A student must be able to pass an exam using ONLY this material.

MANDATORY DEPTH RULES:
1. BULLETS: Minimum 4 bullets per slide. Each bullet = 2-3 complete sentences including: the concept, the formula/equation in LaTeX ($...$), a concrete example or consequence, and the examiner's angle (what aspect is commonly tested). Never write one-liners.
2. DEFINITIONS: 'term' = precise technical name. 'body' = 4-6 sentences covering: formal definition, intuition/analogy, key properties, common misconceptions, real-world application. 'notation' = the exact mathematical symbol or formula (use LaTeX).
3. THEOREMS: 'statement' = full formal statement with all conditions. 'proof_sketch' = 5-8 step proof outline, each step as a complete sentence explaining the mathematical move.
4. DERIVATIONS: Each 'step' MUST have: 'label' = step name (e.g. "Let S_n"), 'math' = LaTeX expression (e.g. "a + (n-1)d"), 'note' = one sentence explaining WHY this step is valid.
5. EXAMPLES: 'problem' = a complete exam-style question with all given values. 'solution' = 5-8 steps, each a complete sentence showing calculation. 'answer' = final numerical or algebraic result.
6. TIKZ/MATPLOTLIB: Set 'code' to "" for tikz. Write a highly descriptive 'caption' (3-4 sentences describing diagram elements, labels, colors).
7. SUMMARIES: 'takeaways' = 5-6 bullet points, each summarizing a key exam topic.
8. Ground every fact in the provided context. Never hallucinate.

CRITICAL — ALLOWED SLIDE TYPES (use EXACTLY these strings, no variants):
  section | bullets | image | diagram | quote | summary | theorem | derivation | example | definition | tikz | matplotlib
NEVER use: "bullet" (must be "bullets"), "reference", "references", or any other string not in the list above.

Return your response as a JSON object matching this schema:
{
  "slides": [
    {
      "type": "bullets",
      "title": "Slide Title",
      "bullets": ["full sentence 1 with $formula$...", "full sentence 2..."],
      "source": {"doc": "doc_name", "loc": "page/sec"}
    },
    ...
  ]
}
Do NOT output markdown blocks, backticks, or explanation. Start directly with the JSON object."""

TIKZ_GENERATION_SYSTEM_PROMPT = """You are an expert LaTeX TikZ developer.
Your task is to write clean, compile-ready, and visually stunning TikZ code (verbatim tikzpicture content, excluding \\begin{{tikzpicture}} and \\end{{tikzpicture}}) for multiple diagrams or flowcharts.

CRITICAL RULES:
1. Output ONLY the raw TikZ commands (e.g. \\draw, \\node, \\fill, etc.) that go inside the \\begin{{tikzpicture}} and \\end{{tikzpicture}} block.
2. Do NOT include \\begin{{tikzpicture}} or \\end{{tikzpicture}} in the code.
3. Custom Colors: The following colors are ALREADY DEFINED — you MUST use them for styling:
   - brandDark (slate dark, #1A252C)
   - brandTeal (deep teal, #006D77)
   - brandMint (mint, #83C5BE)
   - waterBlue (cyan, #00B4D8)
   - waterDeep (blue, #0077B6)
   - earthBrown (clay, #DDA15E)
   - earthDark (earth, #BC6C25)
   - accentOrange (coral, #E76F51)
   - lightBg (off-white, #F8F9FA)
       - forestGreen (green, #2A9D8F)
    - paleYellow (light yellow, #FFFACD)
4. CRITICAL — ONLY use colors from the list above. NEVER invent undefined color names. If you need a lighter variant, use `color!opacity` syntax (e.g. `waterBlue!30`). Using any undefined color will crash the compile and ruin the entire deck.
5. Never use a backslash before ordinary English words (e.g. `the`, `Smiled`, `thieves`). A backslash-word creates a LaTeX command, not emphasis. Use `textbf{word}` or `textit{word}` for emphasis.
6. If you use inline math, every `\\(` must have a matching `\\)`.
8. KEEP CODE CONCISE: Maximum 40 lines of TikZ per diagram. Use ONLY standard TikZ primitives (\\draw, \\node, \\fill, \\path). Do NOT define \\tikzset, \\newcommand, or custom styles — these cause JSON truncation.
9. Arrows: Use -{Stealth[scale=1.2]} for flow arrows.
10. Dimensions: Scale the diagram to fit within 10cm x 7cm.
11. No diagram element (node center, line endpoint, text) may be within 0.3cm of the canvas boundary rectangle. Keep all content within the inner safe area.
12. Every labeled diagram must include descriptive \\node text labels on key elements — never leave a diagram with unlabeled nodes or axes.
13. Return your response as a single valid JSON object with the following structure:
{
  "diagrams": [
    {
      "title": "Diagram Title",
      "code": "verbatim TikZ code commands here"
    }
  ]
}
Do NOT output markdown blocks, backticks, or explanation. Start directly with the JSON object.

FEW-SHOT EXAMPLES:
---
Example 1: Orthogonal vectors (right angle)
Title: "Orthogonal Vectors — Right Angle"
Code:
\\draw[brandTeal, thick, -{Stealth[scale=1.2]}] (0,0) -- (3,1) node[midway, above] {$\\vec{a}$};
\\draw[brandMint, thick, -{Stealth[scale=1.2]}] (0,0) -- (1,-3) node[midway, right] {$\\vec{b}$};
\\draw[accentOrange, thick] (0.3,0.1) -- (0.4,0.3) -- (0.2,0.4) -- cycle;
\\node[brandDark, font=\\footnotesize] at (0.8, -0.5) {$90^\\circ$};
\\node[brandDark, font=\\footnotesize] at (3.5, 1.2) {$\\vec{a} \\cdot \\vec{b} = 0$};
---
Example 2: Orthogonal projection
Title: "Orthogonal Projection of a onto b"
Code:
\\draw[brandTeal, thick, -{Stealth[scale=1.2]}] (0,0) -- (4,0) node[above, midway] {$\\vec{b}$};
\\draw[brandMint, thick, -{Stealth[scale=1.2]}] (0,0) -- (2.5,2.5) node[midway, above left] {$\\vec{a}$};
\\draw[accentOrange, thick, dashed] (2.5,2.5) -- (2.5,0);
\\draw[accentOrange, thick, -{Stealth[scale=1.2]}] (0,0) -- (2.5,0) node[below, midway] {$\\text{proj}_{\\vec{b}}\\vec{a}$};
\\node[brandDark, font=\\footnotesize] at (3.5, 0.5) {$\\frac{\\vec{a}\\cdot\\vec{b}}{\\vec{b}\\cdot\\vec{b}}\\vec{b}$};
\\draw[brandDark, thick, -{Stealth[scale=1.0]}] (2.5,0.1) -- (2.5,0.5) node[above, font=\\tiny] {$\\perp$};
---
Example 3: Gram-Schmidt flowchart
Title: "Gram-Schmidt Orthogonalization Process"
Code:
\\node[draw, rounded corners, fill=brandTeal!20, text width=3.5cm, align=center, font=\\small\\bfseries] (v1) at (0,3) {$\\vec{v}_1 \\rightarrow \\vec{u}_1$};
\\node[draw, rounded corners, fill=brandMint!20, text width=3.5cm, align=center, font=\\small\\bfseries] (v2) at (5,3) {$\\vec{v}_2 \\rightarrow \\text{proj}_{\\vec{u}_1}\\vec{v}_2$};
\\node[draw, rounded corners, fill=waterBlue!20, text width=3.5cm, align=center, font=\\small\\bfseries] (v3) at (10,3) {$\\vec{u}_2 = \\vec{v}_2 - \\text{proj}$};
\\node[draw, rounded corners, fill=forestGreen!20, text width=3.5cm, align=center, font=\\small\\bfseries] (q1) at (0,-1) {$\\vec{q}_1 = \\frac{\\vec{u}_1}{\\lVert\\vec{u}_1\\rVert}$};
\\node[draw, rounded corners, fill=forestGreen!20, text width=3.5cm, align=center, font=\\small\\bfseries] (q2) at (10,-1) {$\\vec{q}_2 = \\frac{\\vec{u}_2}{\\lVert\\vec{u}_2\\rVert}$};
\\draw[-{Stealth[scale=1.2]}, brandTeal, thick] (v1) -- (v2);
\\draw[-{Stealth[scale=1.2]}, brandTeal, thick] (v2) -- (v3);
\\draw[-{Stealth[scale=1.2]}, forestGreen, thick] (v1) -- (q1);
\\draw[-{Stealth[scale=1.2]}, forestGreen, thick] (v3) -- (q2);
\\node[brandDark, font=\\footnotesize] at (5, 4) {Subtract projections};
\\node[brandDark, font=\\footnotesize] at (5, -1.8) {Normalize};"""

TIKZ_SINGLE_SYSTEM_PROMPT = TIKZ_GENERATION_SYSTEM_PROMPT.replace(
    "for multiple diagrams or flowcharts.",
    "for exactly one diagram or flowchart.",
).replace(
    '13. Return your response as a single valid JSON object with the following structure:\n{\n  "diagrams": [\n    {\n      "title": "Diagram Title",\n      "code": "verbatim TikZ code commands here"\n    }\n  ]\n}',
    '13. Return your response as a single valid JSON object with the following structure:\n{\n  "code": "verbatim TikZ code commands here"\n}',
)

MERMAID_SINGLE_SYSTEM_PROMPT = """You are an expert Mermaid diagram developer.
Your task is to write clean, compile-ready Mermaid diagram code for exactly one diagram.

CRITICAL RULES:
1. Output ONLY a valid Mermaid diagram code block.
2. Return your response as a single valid JSON object with the following structure:
{
  "code": "mermaid diagram code here"
}
3. Keep the diagram simple (max 10-12 nodes). Use TD (top-down) flowcharts, sequence diagrams, or state diagrams as appropriate.
4. Wrap node labels in double quotes (e.g. A["Label Text"]) to avoid syntax errors with special characters.
Do NOT output markdown blocks, backticks, or explanation. Start directly with the JSON object.

FEW-SHOT EXAMPLES:
---
Example 1: Gram-Schmidt algorithm flowchart
Code:
flowchart TD
    A["Start: Input vectors v₁,v₂,...,vₙ"] --> B["Set u₁ = v₁"]
    B --> C["For i = 2 to n:"]
    C --> D["Compute proj = Σⱼ₌₁ᵢ⁻¹ (vᵢ·uⱼ)/(uⱼ·uⱼ) uⱼ"]
    D --> E["Set uᵢ = vᵢ - proj"]
    E --> F["Normalize: qᵢ = uᵢ/‖uᵢ‖"]
    F --> G{"i < n?"}
    G -->|Yes| D
    G -->|No| H["Output orthonormal basis {q₁,...,qₙ}"]
---
Example 2: Orthogonal matrix state diagram
Code:
stateDiagram-v2
    [*] --> IDENTITY: QᵀQ = I
    IDENTITY --> ROTATION: det = +1
    IDENTITY --> REFLECTION: det = -1
    ROTATION --> ORTHOGONAL: Q⁻¹ = Qᵀ
    REFLECTION --> ORTHOGONAL: Q⁻¹ = Qᵀ
    ORTHOGONAL --> [*]"""

GRAPHVIZ_SINGLE_SYSTEM_PROMPT = """You are an expert Graphviz DOT developer.
Your task is to write clean, compile-ready DOT code for exactly one diagram.

CRITICAL RULES:
1. Output ONLY valid Graphviz DOT language code.
2. Return your response as a single valid JSON object with the following structure:
{
  "code": "digraph G { ... }"
}
3. Use rankdir=LR for pipelines/processes, rankdir=TB for hierarchies.
4. Use shape="box", style="rounded,filled", fillcolor="#F8F9FA", color="#CDC6B6" for elegant defaults.
Do NOT output markdown blocks, backticks, or explanation. Start directly with the JSON object.

FEW-SHOT EXAMPLES:
---
Example 1: Four fundamental subspaces
Code:
digraph G {
    rankdir=TB;
    node [shape=box, style="rounded,filled", fillcolor="#F8F9FA", color="#CDC6B6", fontname="Helvetica"];
    edge [color="#333333", fontname="Helvetica"];
    subgraph cluster_Rn {
        label = "Rⁿ (domain)";
        style=dashed;
        RowSpace [label="Row Space C(Aᵀ)", fillcolor="#E8F5E9"];
        NullSpace [label="Nullspace N(A)", fillcolor="#FFF3E0"];
    }
    subgraph cluster_Rm {
        label = "Rᵐ (codomain)";
        style=dashed;
        ColSpace [label="Column Space C(A)", fillcolor="#E3F2FD"];
        LeftNull [label="Left Nullspace N(Aᵀ)", fillcolor="#FCE4EC"];
    }
    RowSpace -> NullSpace [label="⊥", color="#E76F51", penwidth=2.0];
    ColSpace -> LeftNull [label="⊥", color="#E76F51", penwidth=2.0];
    RowSpace -> ColSpace [label="A", color="#006D77", style=dashed];
    NullSpace -> LeftNull [label="Aᵀ", color="#006D77", style=dashed];
}
---
Example 2: Orthogonal matrix properties timeline
Code:
digraph G {
    rankdir=LR;
    node [shape=box, style="rounded,filled", fillcolor="#F8F9FA", color="#CDC6B6", fontname="Helvetica"];
    edge [color="#333333", fontname="Helvetica"];
    Q [label="Orthogonal Matrix Q"];
    Q -> QTQ [label="QᵀQ = I"];
    QTQ -> INV [label="Q⁻¹ = Qᵀ"];
    INV -> DET [label="det(Q) = ±1"];
    DET -> PRESERVE [label="Preserves ‖x‖, angles"];
    PRESERVE -> EIGEN [label="Eigenvalues on unit circle"];
}
"""

QA_EVALUATOR_SYSTEM_PROMPT = """You are an academic curriculum QA inspector.
Given a slide spec for a subtopic, review it for academic depth, logical flow, formatting, and mathematical accuracy.

CRITICAL CHECKLIST:
1. Academic Depth: Are explanations and examples sufficiently detailed for exam prep? Shallow bullet points must be rejected.
2. Formatting: Are equations correctly typeset? Are definitions clear?
3. Terminology: Is the terminology consistent?
4. Density: Are there at most 6 bullets per slide?

Return your evaluation as a strict JSON object with this structure:
{
  "pass": boolean,
  "feedback": "detailed description of issues if pass is false, or empty string if pass is true"
}
Do NOT output markdown blocks, backticks, or explanation. Start directly with the JSON object."""

BRIEF_EXPANSION_SYSTEM_PROMPT = """You are a curriculum design assistant. Given a short topic or chapter title,
expand it into a structured content brief. Do not generate content yet — only scope the assignment.

Return strict JSON matching this schema:
{
  "topic": string,
  "inferred_subject_domain": string,
  "inferred_audience_level": "beginner" | "intermediate" | "advanced",
  "assumed_prerequisites": string[],
  "suggested_scope": string,
  "depth_level": "survey" | "standard" | "deep-dive",
  "estimated_slide_count": integer,
  "initial_glossary": [{"term": string, "definition": string}]
}
Output JSON only."""

PLANNING_SYSTEM_PROMPT = """You are a university professor. Given a topic and its content brief,
decompose the topic into logical, distinct subtopics (between 4 and 8 subtopics).
For each subtopic, provide a measurable learning objective using Bloom's Taxonomy.
Define the prerequisite relationships between subtopics (which subtopic must be covered before another).

Return strict JSON matching this schema:
{
  "subtopics": [
    {
      "id": string,
      "title": string,
      "bloom_level": "remember" | "understand" | "apply" | "analyze" | "evaluate" | "create",
      "objective": string
    }
  ],
  "dependencies": [
    {"from": string, "to": string, "reason": string}
  ]
}
Output JSON only. Ensure the dependencies do not form cycles."""

CONCEPT_EXPANSION_SYSTEM_PROMPT = """You are a Senior Academic Content Writer producing EXAM-READY lecture notes.
Expand the given subtopic into complete instructional content. Use ONLY the provided source context for factual claims; if a claim isn't grounded in context, state it as general/common knowledge explicitly or omit it.

Style rules:
- Audience level: {audience_level}
- Maintain terminology exactly as defined in the glossary (do not introduce synonyms for defined terms).
- Every equation gets a plain-language sentence directly before or after it.
- Every new term is bolded on first use.

Return strict JSON matching this schema:
{{
  "heading": string,
  "explanation_md": string,
  "worked_example_md": string,
  "analogy_md": string,
  "diagram_spec": {{"needed": boolean, "type": "flow"|"architecture"|"plot"|"geometric"|"none", "description": string}},
  "derivation_md": string or null,
  "intuition_md": string,
  "application_md": string,
  "exercises": [{{"prompt": string, "difficulty": "easy"|"medium"|"hard", "solution_md": string}}],
  "summary_md": string,
  "new_glossary_terms": [{{"term": string, "definition": string}}]
}}
Do NOT output markdown blocks, backticks, or explanation. Start directly with the JSON object."""


def call_llm_with_fallback(
    messages: list,
    role: str,
    key_index: int = 0,
    max_tokens: int = 32768,
    response_schema: dict | None = None,
    reasoning_effort: str = "high",
    thinking_disabled: bool = False,
) -> str:
    """DeepSeek V4 Pro only, then Groq 4-key fallback.

    reasoning_effort: forced to "high" for DeepSeek. Non-DeepSeek providers ignore it.
    thinking_disabled: when True, DeepSeek will not use thinking mode (saves
    tokens for simple structured JSON tasks like outlines).
    """
    from llm import call_deepseek, call_groq_content, call_groq_orchestrator, call_groq_tikz, call_groq
    import config
    import cancel_flag
    cancel_flag.check()

    # 1. DeepSeek V4 Pro (primary generation — reasoning=high, thinking enabled, 32K tokens)
    if config.DEEPSEEK_API_KEY:
        try:
            print(f"[generate] DeepSeek V4 Pro ({config.DEEPSEEK_MODEL}) for role '{role}' (reasoning_effort={reasoning_effort})...")
            return call_deepseek(messages, max_tokens=max_tokens, reasoning_effort=reasoning_effort, thinking_disabled=thinking_disabled)
        except Exception as e:
            print(f"[generate] DeepSeek failed ({e}). Trying Groq 4-key fallback...")

    # 2. Groq 4-key fallback (sequential, role-aware)
    if config.GROQ_KEYS:
        for idx in range(min(4, len(config.GROQ_KEYS))):
            try:
                print(f"[generate] Groq key index {idx} for role '{role}'...")
                if role in ("orchestrator", "pptx_plan"):
                    return call_groq_orchestrator(messages, max_tokens=max_tokens)
                elif role == "content":
                    return call_groq_content(messages, key_index=idx, max_tokens=max_tokens)
                elif role in ("tikz", "mermaid", "graphviz"):
                    return call_groq_tikz(messages, max_tokens=max_tokens)
                else:
                    return call_groq(messages, max_tokens=max_tokens)
            except Exception as e:
                print(f"[generate] Groq key {idx} failed: {e}")

    raise RuntimeError(f"All generation providers exhausted for role '{role}'.")


# ---------------------------------------------------------------------------
# McKinsey-style PPTX plan (separate from the exam-depth PDF spec)
# ---------------------------------------------------------------------------

# JSON schema block embedded in the prompt — works across ALL providers
# without needing provider-specific structured-output API features.
_PPTX_SCHEMA_BLOCK = """
Return ONLY a valid JSON object (no markdown, no preamble) matching this schema exactly:
{
  "presentation_title": "<6–8 word title>",
  "subject": "<academic subject>",
  "slides": [
    {
      "title": "<6-word slide title>",
      "bullets": ["<≤12 words>", "<≤12 words>", "<≤12 words>", "<≤12 words>"],
      "speaker_notes": "<full textbook explanation, formulas, derivations — 3+ sentences>"
    }
  ]
}"""

PPTX_PLAN_SYSTEM_PROMPT = (
    "You are an expert presentation designer and university professor. "
    "Your task is to distill academic context into a highly engaging, "
    "professional McKinsey-style presentation for undergraduate students.\n\n"
    "DESIGN RULES — follow without exception:\n"
    "1. Brevity is Law: bullets must be punchy, readable from the back of a classroom.\n"
    "2. Rule of 4: never exceed 4 bullet points per slide.\n"
    "3. Word Limit: never exceed 12 words per bullet point. No complete sentences.\n"
    "4. Slide Count: produce exactly 10–14 slides covering the topic end-to-end.\n"
    "5. Speaker Notes: move ALL complex derivations, textbook paragraphs, proofs, "
    "and detailed worked examples into speaker_notes. The slide canvas is for the "
    "audience; the notes are for the teacher.\n"
    "6. Cover these narrative beats in order: motivation → definition → key properties → "
    "intuition/geometry → worked example → applications → summary.\n"
    + _PPTX_SCHEMA_BLOCK
)

SLIDE_SPEC_SYSTEM_PROMPT = """You are a Senior Academic Content Writer and university professor producing EXAM-READY lecture slide decks.
Your task is to generate a complete slide deck specification in JSON.
Every slide must be highly detailed, factually accurate, and dense enough that a student could pass an exam using ONLY this material.

MANDATORY DEPTH RULES (Slides must be fully filled; no thin or empty slides):
1. BULLETS: Minimum 4 bullets per slide (typically 4-6). Each bullet must be 2-3 complete sentences covering: the concept, any relevant formulas/equations in LaTeX ($...$), concrete examples/consequences, and the examiner's angle (what aspect is commonly tested). Never write one-liners or short fragments.
2. DEFINITIONS: 'term' = precise technical name. 'body' = 4-6 sentences covering formal definition, intuition/analogy, key properties, common misconceptions, and real-world applications. 'notation' = exact mathematical symbol or formula (use LaTeX).
3. THEOREMS: 'statement' = full formal statement with all conditions. 'proof_sketch' = 5-8 step proof outline, each step as a complete sentence explaining the mathematical move.
4. DERIVATIONS: Each step in 'steps' MUST have: 'label' = step name/name of the equation, 'math' = LaTeX expression, 'note' = one sentence explaining WHY this step is valid.
5. EXAMPLES: 'problem' = complete exam-style question with given values. 'solution' = 5-8 steps, each a complete sentence showing calculation. 'answer' = final numerical or algebraic result.
6. TIKZ/MERMAID/GRAPHVIZ/MATPLOTLIB: Dynamic selection: Math-related topics (Mathematics, Physics, Chemistry) MUST use 'tikz' or 'matplotlib' slides. Computer Science and flowchart-heavy slides MUST use 'mermaid' slides. Provide compile-ready code in the 'code' field. Keep TikZ diagrams within 10cm x 7cm and use ONLY pre-defined colors (brandDark, brandTeal, brandMint, waterBlue, waterDeep, earthBrown, earthDark, accentOrange, lightBg, forestGreen, paleYellow). Write a highly descriptive 'caption' (3-4 sentences describing the diagram elements).
7. IMAGES: If the retrieved context contains image chunks (labeled as `[i] IMAGE image_path=...`), you MUST include an 'image' slide referencing that exact 'image_path' and its 'caption' to ensure visual richness. Do NOT invent image paths that are not present in the context.
8. SECTIONS: Use 'section' slides sparingly as chapter divider slides (max 2-3 per deck). Do NOT create a section slide for every single slide.
9. NO META-COMMENTARY: Do NOT output any writer notes, thoughts, reasoning, terminology debates, or meta-commentary inside the JSON fields. Output only finalized academic content.

CRITICAL FORMATTING RULES:
- Use LaTeX math notation (e.g. $x^2$, $a_{ij}$) for all mathematical symbols, variables, and equations.
- Every LaTeX math block must be fully contained within a single bullet string (do not split LaTeX delimiters $...$, \\(...\\), \\[...\\], or \\begin{bmatrix}...\\end{bmatrix} across separate bullet points).
- NEVER use double quotes (") inside JSON string values. For quotes, emphasis, or labels, use single quotes (') instead. Double quotes inside JSON string values will break the JSON parser.
- Never output markdown formatting blocks, backticks (e.g. ```json), or explanatory text outside the JSON. Start directly with the JSON object.
- Return your response as a single valid JSON object matching the schema.

JSON SCHEMA:
{
  "title": "Main Presentation Title",
  "subtitle": "Sub-title or Subject domain",
  "query": "The original query string",
  "slides": [
    {
      "type": "section" | "bullets" | "image" | "diagram" | "quote" | "summary" | "theorem" | "derivation" | "example" | "definition" | "tikz" | "matplotlib" | "mermaid" | "graphviz",
      "title": "Slide Title",
      "bullets": ["bullet 1...", "bullet 2..."],
      "image": "image path (only if using retrieved database images)",
      "caption": "figure caption",
      "explains": ["explanation bullet 1..."],
      "text": "quote text",
      "takeaways": ["takeaway bullet 1..."],
      "statement": "formal theorem statement",
      "proof_sketch": ["proof step 1..."],
      "steps": [{"label": "Step 1", "math": "LaTeX", "note": "explanation"}],
      "result": "final result",
      "problem": "example problem text",
      "solution": ["solution step 1..."],
      "answer": "final answer",
      "term": "defined term",
      "body": "definition text",
      "notation": "LaTeX notation",
      "code": "code block for tikz/matplotlib/mermaid/graphviz"
    }
  ]
}
"""


_TYPE_ALIASES = {
    "bullet": "bullets",
    "bullet_slide": "bullets",
    "reference": "references",
    "derivations": "derivation",
    "examples": "example",
    "definitions": "definition",
    "theorems": "theorem",
    "sections": "section",
}
_VALID_TYPES = frozenset([
    "section", "bullets", "image", "diagram", "quote", "summary",
    "theorem", "derivation", "example", "definition", "tikz", "matplotlib",
    "mermaid", "graphviz",
])


def _canonical_slide_type(value: object) -> str:
    stype = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _TYPE_ALIASES.get(stype, stype)


def generate_spec(
    query: str,
    subject: str | None = None,
    learner_profile: dict | None = None,
    target_slide_count: int | None = None,
) -> dict:
    print(f"[generate] Retrieving chunks...")
    
    # 2. Retrieve chunks (fetch more for single-pass context richness)
    chunks, reranked = retrieve(query, k=6, subject=subject, learner_profile=learner_profile)
    if not chunks:
        print(f"[generate] Retrieval returned 0 chunks. Refusing to generate.")
        raise ValueError(f"Retrieval returned 0 chunks for query '{query}'. Gating failed.")
        
    # 3. Confidence score gating
    top_score = chunks[0].get("score", 0.0)
    threshold = RERANK_THRESHOLD if reranked else SCORE_THRESHOLD
    if top_score < threshold:
        print(f"[generate] Top similarity score {top_score:.4f} is below threshold {threshold}. Refusing.")
        raise ValueError(f"Retrieval confidence score {top_score:.4f} is below threshold {threshold} for query '{query}'. Gating failed.")

    # Prepend Wikipedia summary for extra factual grounding
    web_ctx = _web_supplement(query)
    context_str = build_context(chunks)
    if web_ctx:
        context_str = web_ctx + "\n\n" + context_str

    print(f"[generate] Generating slide deck spec via single LLM pass...")
    
    # Add subject-specific guidance if any
    addon = SUBJECT_ADDONS.get(subject or "", "")
    system_prompt = SLIDE_SPEC_SYSTEM_PROMPT
    if addon:
        system_prompt += "\n\n" + addon

    try:
        from llm import generate_spec_with_llm
        raw_spec = generate_spec_with_llm(query, system_prompt, context_str)
        spec = _parse_json(raw_spec)
    except Exception as e:
        print(f"[generate] Single-pass generation failed ({e}).")
        raise ValueError(f"Failed to generate slide spec: {e}")

    # Inject defaults / normalize properties
    spec.setdefault("title", query.title())
    spec.setdefault("subtitle", f"Exam Preparation — {subject.replace('_', ' ').title() if subject else 'Core Concepts'}")
    spec.setdefault("query", query)
    spec.setdefault("slides", [])

    filtered_slides = []
    for idx, slide in enumerate(spec.get("slides", []), start=1):
        if not isinstance(slide, dict):
            continue
        slide["type"] = _canonical_slide_type(slide.get("type"))
        stype = slide["type"]
        if stype == "references":
            continue
        if stype not in _VALID_TYPES:
            print(f"[generate] Warning: unknown slide type '{stype}' → demoting to 'bullets'")
            slide["type"] = "bullets"
            if "bullets" not in slide:
                slide["bullets"] = [slide.get("caption") or slide.get("text") or slide.get("title") or "(content unavailable)"]
        if "source" in slide:
            if isinstance(slide["source"], str):
                slide["source"] = {"doc": slide["source"], "loc": ""}
        slide["number"] = idx
        filtered_slides.append(slide)
    
    spec["slides"] = filtered_slides

    # Validate against JSON schema
    try:
        validate(spec, SCHEMA)
    except Exception as e:
        print(f"[generate] Schema validation failed: {e}")
        # Bounded try to repair JSON
        repair_messages = [
            {"role": "system", "content": (
                "Repair the following malformed slide spec JSON to match the Beamer/PPTX schema. "
                "Output JSON only."
            )},
            {"role": "user", "content": json.dumps(spec, indent=2)},
        ]
        try:
            repaired_raw = call_llm_with_fallback(repair_messages, role="orchestrator", max_tokens=16384)
            repaired_spec = _parse_json(repaired_raw)
            validate(repaired_spec, SCHEMA)
            spec = repaired_spec
        except Exception as e_repair:
            raise ValueError(f"JSON validation failed and repair also failed: {e_repair}. Original validation: {e}")

    return spec


def repair_diagram_code(stype: str, code: str, error_log: str) -> str:
    """LLM pass to fix compile errors in generated diagram/chart code."""
    print(f"[generate] Repairing {stype} code due to compile failure...")
    
    if stype == "tikz":
        system_prompt = TIKZ_SINGLE_SYSTEM_PROMPT
    elif stype == "mermaid":
        system_prompt = MERMAID_SINGLE_SYSTEM_PROMPT
    elif stype == "graphviz":
        system_prompt = GRAPHVIZ_SINGLE_SYSTEM_PROMPT
    else:
        return code
        
    repair_prompt = (
        f"The following {stype.upper()} code failed to compile:\n\n"
        f"CODE:\n{code}\n\n"
        f"COMPILE ERROR:\n{error_log}\n\n"
        f"Please fix the syntax/compilation errors and output ONLY the corrected {stype.upper()} code inside the 'code' key of the JSON structure."
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": repair_prompt},
    ]
    try:
        raw = call_llm_with_fallback(
            messages, role=stype, max_tokens=8192,
            response_schema={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        )
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:]).strip()
        data = _parse_json(raw)
        fixed = data.get("code", "").strip()
        if stype == "tikz" and fixed:
            match = re.search(r"\\begin\{tikzpicture\}(.*?)\\end\{tikzpicture\}", fixed, re.DOTALL)
            if match:
                fixed = match.group(1).strip()
        return fixed or code
    except Exception as e:
        print(f"[generate] Diagram repair failed: {e}")
        return code


# ---------------------------------------------------------------------------
# Helper: compress a generated spec into plain-text context for PPTX plan LLM
# ---------------------------------------------------------------------------

def _spec_to_context(spec: dict, max_chars: int = 7000) -> str:
    """Extract readable text from a rendered slide spec to feed the McKinsey LLM.

    Avoids a second RAG retrieval — the spec already has all the content.
    """
    lines: list[str] = [f"TOPIC: {spec.get('title', '')}"]
    for s in spec.get("slides", []):
        t = s.get("type", "")
        title = s.get("title") or s.get("term") or ""
        if title:
            lines.append(f"\n## {title}")
        if t == "bullets":
            for b in s.get("bullets", []):
                lines.append(f"  - {b}")
        elif t == "definition":
            lines.append(s.get("body", ""))
            if s.get("notation"):
                lines.append(f"  Notation: {s['notation']}")
        elif t == "theorem":
            lines.append(s.get("statement", ""))
            for step in s.get("proof_sketch", []):
                lines.append(f"    * {step}")
        elif t == "derivation":
            for step in s.get("steps", []):
                label = step.get("label", "")
                math  = step.get("math", "")
                note  = step.get("note", "")
                lines.append(f"    {label + ': ' if label else ''}{math}" + (f" ({note})" if note else ""))
            if s.get("result"):
                lines.append(f"  Result: {s['result']}")
        elif t == "example":
            lines.append(f"  Problem: {s.get('problem', '')}")
            for step in s.get("solution", []):
                lines.append(f"    {step}")
            if s.get("answer"):
                lines.append(f"  Answer: {s['answer']}")
        elif t == "summary":
            for take in s.get("takeaways", []):
                lines.append(f"  ★ {take}")
    ctx = "\n".join(lines)
    return ctx[:max_chars]


def generate_pptx_plan(query: str, context: str, subject: str | None) -> "PresentationPlan":
    """Call LLM with McKinsey system prompt → validate with Pydantic → PresentationPlan.

    Uses the same provider fallback chain as call_llm_with_fallback but passes
    the dedicated PPTX_PLAN_SYSTEM_PROMPT.  JSON output is validated via
    Pydantic; on validation error a best-effort recovery is attempted.
    """
    from schemas import PresentationPlan
    from pydantic import ValidationError

    context_block = (
        f"ACADEMIC CONTEXT (from course materials):\n{context}\n"
        if context.strip()
        else "No indexed course material matched this topic — draw on your own subject knowledge instead.\n"
    )
    user_content = (
        f"{context_block}\n"
        f"TOPIC: {query}\n"
        f"SUBJECT: {subject or 'General'}\n\n"
        "Now produce the presentation JSON following all design rules."
    )
    messages = [
        {"role": "system", "content": PPTX_PLAN_SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    raw = call_llm_with_fallback(messages, role="pptx_plan", max_tokens=6000)

    # Strip markdown fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(
            lines[1:-1] if lines[-1].startswith("```") else lines[1:]
        ).strip()

    data = _parse_json(raw)

    # Inject defaults if LLM omitted top-level fields
    data.setdefault("presentation_title", query.title())
    data.setdefault("subject", (subject or "General").replace("_", " ").title())
    data.setdefault("slides", [])

    try:
        plan = PresentationPlan(**data)
    except ValidationError as exc:
        print(f"[generate] PresentationPlan validation errors: {exc.error_count()} — attempting recovery")
        # Recover: normalise slides list
        clean_slides = []
        for raw_s in data.get("slides", []):
            if not isinstance(raw_s, dict):
                continue
            raw_s.setdefault("title", "Key Concepts")
            raw_s.setdefault("bullets", [])
            raw_s.setdefault("speaker_notes", "")
            # Truncate oversized bullets
            raw_s["bullets"] = [b[:120] for b in raw_s["bullets"][:4]]
            clean_slides.append(raw_s)
        data["slides"] = clean_slides
        plan = PresentationPlan(**data)   # raises if still broken

    return plan


def run(
    query: str,
    subject: str | None = None,
    grade_level: str | None = None,
    target_slide_count: int | None = None,
) -> Path:
    # Teacher outputs disabled by request: no teacher_ppt.pdf, no slides.pptx.
    # generate_spec() and process_slides() are skipped; student notes and
    # assignment are the only user-facing deliverables. grade_level and
    # target_slide_count are ignored here — they only shaped the teacher deck.
    _ = grade_level, target_slide_count  # ponytail: silence unused, keep sig for callers
    slug = _slug(query)
    out_dir = OUTPUT / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    import notes as _notes
    import assignment as _assignment
    notes_pdf = _notes.run(query, subject=subject)
    print(f"[generate] student notes -> {notes_pdf.relative_to(ROOT)}")
    student_asn, teacher_asn = _assignment.run(query, subject=subject)
    print(f"[generate] assignment student -> {student_asn.relative_to(ROOT)}")
    print(f"[generate] assignment teacher -> {teacher_asn.relative_to(ROOT)}")
    return notes_pdf


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit('usage: python generate.py "topic query" [--subject SUBJECT]')
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--subject", default=None)
    a = ap.parse_args()
    run(" ".join(a.query), subject=a.subject)
