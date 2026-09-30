import json
from pathlib import Path

from config import OUTPUT
from generate import retrieve, build_context, call_llm_with_fallback, _parse_json, _slug

QUESTIONS_SYSTEM_PROMPT = """You are a senior university professor creating exam-ready assignments from retrieved academic content. Your questions test deep understanding, not surface recall.

Return ONLY a single JSON object (no markdown, no commentary, no preamble). Start with `{` and end with `}`.

{
  "title": str,
  "subject": str,
  "total_marks": int,
  "questions": [
    {
      "num": int,
      "type": "mcq" | "short" | "long",
      "text": str,              // PLAIN ENGLISH ONLY. No math notation.
      "marks": int,
      "options": [str, str, str, str],  // required only for mcq
      "math_blocks": [str],             // ALL equations, variables with sub/superscripts, and Greek letters go here
      "tikz_code": str,                 // optional FULL tikzpicture body (no \\begin{tikzpicture}), or ""
      "mermaid_code": str,              // optional FULL mermaid definition, or ""
      "graphviz_code": str,             // optional FULL graphviz DOT code, or ""
      "matplotlib_code": str,           // optional FULL matplotlib code, or ""
      "image": str,                     // optional path to database image from context, or ""
      "caption": str                    // caption for diagram/image, or ""
    }
  ]
}

HARD RULES:

1. QUESTION TEXT IS PLAIN ENGLISH ONLY.
   The `text` field is rendered as pdflatex text-mode. ANY math notation in `text` becomes gibberish.
   - DO NOT write "x^2", "c^T x", "x_i", "alpha = 0.05", ">= 0", etc. in text.
   - DO write: "x squared", "the transpose of c times x", "x sub i", "alpha equals 0.05", "at least zero".
   - ALL equations and Greek letters go in `math_blocks` using \\ge, \\le, \\alpha, x^2, x_i, c^\\mathsf{T} etc.

2. JSON SYNTAX RULES — OUTPUT VALID JSON.
   - ALL property names and string values MUST use DOUBLE QUOTES (`"`). This is the JSON standard.
   - Inside string values: escape double quotes as `\"`, backslashes as `\\`, newlines as `\n`.
   - Example: a mermaid label `A["Start"]` becomes `"A[\\"Start\\"]"` in JSON.
   - For graphviz labels use single quotes inside the DOT code: `label='Primal LP'` — these do NOT need escaping in JSON.
   - Output a SINGLE valid JSON object. No markdown fences.

3. DIAGRAM COMPLETENESS RULES.
   Every diagram must be SELF-CONTAINED and COMPILE-READY:
   - `tikz_code`: full body with axes, labels, shading. Use $...$ for math. No \\begin{tikzpicture}.
   - `mermaid_code`: start with flowchart/graph/sequenceDiagram. ALL labels in double quotes: A["Start"].
   - `graphviz_code`: use label='...' with single quotes (converted to double quotes by renderer).
   - `matplotlib_code`: full python with inline imports. Ends with no plt.savefig/show.

4. ASSIGNMENT STRUCTURE.
   - Default: 5 MCQ (1 mark each), 3 short-answer (3 marks each), 2 long-answer (10 marks each). Total = 34 marks.
   - Questions must be directly answerable from the CONTEXT.
   - MCQ options: exactly one correct answer, plausible distractors.
   - Short-answer: 3-5 sentence full answers.
   - Long-answer: require derivations, proofs, or multi-step solutions.

5. OUTPUT VALID JSON — all property names and string values use DOUBLE QUOTES (`"`).
   Escape `\"` as `\\\"`, `\\` as `\\\\`, newlines as `\\n`.
   Graphviz labels use single quotes inside DOT: `label='...'`.
   No markdown blocks, backticks, or explanation. Start directly with `{`.

SOLUTIONS_SYSTEM_PROMPT = """You are a senior university professor writing model solutions and marking rubrics. Answers go in text mode — wrap inline math fragments like $x^2$, $c^T x$, $x_i$, $\\alpha$ in $...$ delimiters so pdflatex renders them correctly.

Return ONLY JSON:
{
  "solutions": [
    {"num": int, "answer": str, "explanation": str, "rubric": str, "marks_breakdown": str}
  ]
}
For MCQ: answer = letter (A/B/C/D), explanation = why correct.
For short/long: answer = full model answer, rubric = marking criteria, marks_breakdown = how marks are allocated.
- OUTPUT VALID JSON: all property names and string values use DOUBLE QUOTES (`"`). Escape `\"` as `\\\"`, `\\` as `\\\\`, newlines as `\\n`.
- Wrap inline math like $x^2$, $x_i$, $\\alpha$ etc. with $...$ in answer/explanation text.
- Do NOT output markdown blocks, backticks, or explanation. Start directly with the JSON object."""

QUESTIONS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "subject": {"type": "string"},
        "total_marks": {"type": "integer"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "num": {"type": "integer"},
                    "type": {"type": "string"},
                    "text": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "marks": {"type": "integer"},
                    "image": {"type": "string"},
                    "caption": {"type": "string"},
                    "math_blocks": {"type": "array", "items": {"type": "string"}},
                    "tikz_code": {"type": "string"},
                    "mermaid_code": {"type": "string"},
                    "graphviz_code": {"type": "string"},
                    "matplotlib_code": {"type": "string"},
                },
                "required": ["num", "type", "text", "marks"],
            },
        },
    },
    "required": ["title", "subject", "questions"],
}

SOLUTIONS_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "solutions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "num": {"type": "integer"},
                    "answer": {"type": "string"},
                    "explanation": {"type": "string"},
                    "rubric": {"type": "string"},
                    "marks_breakdown": {"type": "string"},
                },
                "required": ["num", "answer", "explanation", "rubric", "marks_breakdown"],
            },
        },
    },
    "required": ["solutions"],
}


def _assignment_instruction(assignment_type: str | None, question_count: int | None) -> str:
    count = min(max(int(question_count or 0), 1), 100) if question_count else None
    if assignment_type == "mcq":
        return f"Create exactly {count or 10} MCQ questions. Each question must have exactly 4 options and 1 mark."
    if assignment_type == "short":
        return f"Create exactly {count or 10} short-answer questions. Each question is worth 3 marks."
    if assignment_type == "long":
        return f"Create exactly {count or 5} long-answer questions. Each question is worth 10 marks."
    if assignment_type == "math":
        return f"Create exactly {count or 10} mathematical/numerical questions with derivation or calculation steps."
    if count:
        return f"Create exactly {count} mixed exam questions while preserving MCQ/short/long variety."
    return "Create the default balanced assignment described in the system prompt."


def _normalize_assignment_spec(spec: dict, query: str, subject: str | None) -> dict:
    spec = spec if isinstance(spec, dict) else {}
    questions = spec.get("questions", [])
    if not isinstance(questions, list):
        questions = []

    normalized_questions = []
    for idx, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            continue
        qtype = str(question.get("type", "short")).lower()
        if qtype not in {"mcq", "short", "long"}:
            qtype = "long" if "long" in qtype else "short"
        marks = question.get("marks")
        if not isinstance(marks, int):
            marks = 1 if qtype == "mcq" else (10 if qtype == "long" else 3)
        q = {
            "num": int(question.get("num") or idx),
            "type": qtype,
            "text": str(question.get("text") or question.get("question") or ""),
            "marks": marks,
            "image": str(question.get("image", "")),
            "caption": str(question.get("caption", "")),
            "math_blocks": [str(block) for block in question.get("math_blocks", [])] if isinstance(question.get("math_blocks"), list) else [],
            "tikz_code": str(question.get("tikz_code", "")),
            "mermaid_code": str(question.get("mermaid_code", "")),
            "graphviz_code": str(question.get("graphviz_code", "")),
            "matplotlib_code": str(question.get("matplotlib_code", "")),
        }
        if qtype == "mcq":
            options = question.get("options", [])
            if isinstance(options, str):
                options = [options]
            elif not isinstance(options, list):
                options = []
            options = [str(opt) for opt in options[:4]]
            while len(options) < 4:
                options.append(f"Option {chr(65 + len(options))}")
            q["options"] = options
        normalized_questions.append(q)

    solutions = spec.get("solutions", [])
    if not isinstance(solutions, list):
        solutions = []
    normalized_solutions = []
    for idx, solution in enumerate(solutions, 1):
        if not isinstance(solution, dict):
            continue
        normalized_solutions.append({
            "num": int(solution.get("num") or idx),
            "answer": str(solution.get("answer", "")),
            "explanation": str(solution.get("explanation", "")),
            "rubric": str(solution.get("rubric", "")),
            "marks_breakdown": str(solution.get("marks_breakdown", "")),
        })

    return {
        "title": str(spec.get("title") or query),
        "subject": str(spec.get("subject") or subject or ""),
        "total_marks": sum(q.get("marks", 0) for q in normalized_questions) or int(spec.get("total_marks", 0) or 34),
        "questions": normalized_questions,
        "solutions": normalized_solutions,
    }


def generate_assignment_spec(
    query: str,
    subject: str | None = None,
    assignment_type: str | None = None,
    question_count: int | None = None,
    top_k: int = 5,
) -> dict:
    chunks, _ = retrieve(query, k=top_k, subject=subject)
    context = build_context(chunks)
    instruction = _assignment_instruction(assignment_type, question_count)

    q_messages = [
        {"role": "system", "content": QUESTIONS_SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nTopic: {query}\n\nAssignment instruction: {instruction}"},
    ]
    q_text = None
    try:
        q_text = call_llm_with_fallback(
            q_messages,
            role="assignment_questions",
            response_schema=QUESTIONS_RESPONSE_SCHEMA,
        )
        parsed_q = _parse_json(q_text)
    except Exception as e:
        if q_text is None:
            if top_k > 2:
                print(f"[assignment] Questions generation failed ({e}). Retrying with reduced context (top_k={top_k - 2})...")
                return generate_assignment_spec(query, subject, assignment_type, question_count, top_k=top_k - 2)
            raise e
        print(f"[assignment] JSON parsing failed: {e}. Attempting repair...")
        try:
            repair_messages = [
                {"role": "system", "content": (
                    "Repair the following malformed questions JSON into one valid JSON object "
                    "matching the requested questions schema. Output JSON only."
                )},
                {"role": "user", "content": q_text[:20000]},
            ]
            repaired = call_llm_with_fallback(
                repair_messages,
                role="assignment_questions",
                response_schema=QUESTIONS_RESPONSE_SCHEMA,
            )
            parsed_q = _parse_json(repaired)
        except Exception as repair_err:
            print(f"[assignment] Repair pass failed: {repair_err}.")
            if top_k > 2:
                print(f"[assignment] Retrying with reduced context (top_k={top_k - 2})...")
                return generate_assignment_spec(query, subject, assignment_type, question_count, top_k=top_k - 2)
            raise repair_err
    questions_spec = _normalize_assignment_spec(parsed_q, query, subject)

    s_messages = [
        {"role": "system", "content": SOLUTIONS_SYSTEM_PROMPT},
        {"role": "user", "content": f"Questions:\n{json.dumps(questions_spec, indent=2)}\n\nWrite model solutions and rubrics."},
    ]
    s_text = call_llm_with_fallback(
        s_messages,
        role="assignment_solutions",
        response_schema=SOLUTIONS_RESPONSE_SCHEMA,
    )
    solutions_spec = _parse_json(s_text)

    return _normalize_assignment_spec({
        "title": questions_spec.get("title", query),
        "subject": questions_spec.get("subject", subject or ""),
        "total_marks": questions_spec.get("total_marks"),
        "questions": questions_spec.get("questions", []),
        "solutions": solutions_spec.get("solutions", []),
    }, query, subject)


def run(
    query: str,
    subject: str | None = None,
    assignment_type: str | None = None,
    question_count: int | None = None,
) -> tuple[Path, Path]:
    from render_notes import render  # ponytail: lazy import, render_notes is heavy

    spec = generate_assignment_spec(
        query,
        subject,
        assignment_type=assignment_type,
        question_count=question_count,
    )
    slug = _slug(query)
    out_dir = OUTPUT / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "assignment_spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False))

    student_pdf = render(spec, out_dir / "assignment_student.pdf", mode="exam")
    teacher_pdf = out_dir / "assignment_student_teacher.pdf"

    return student_pdf, teacher_pdf
