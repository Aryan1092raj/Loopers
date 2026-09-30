"""Eval harness — retrieval quality first (the metric that matters early).

Loads metadata/eval_questions.json: a list of
    {"q": "...", "must_contain": ["phrase", ...]}
For each question it embeds the query, pulls top-k from Qdrant, and checks
whether the expected phrases appear in the retrieved text. Reports per-question
hits and an aggregate hit@k — i.e. "do the top-k chunks actually contain the
answer". Optionally (--judge) also generates an answer and has Groq score it.

    python src/pipeline.py eval            # retrieval hit@5
    python src/pipeline.py eval --k 3      # hit@3
    python src/pipeline.py eval --judge    # + Groq answer-correctness score
"""
import json

from config import EVAL_QUESTIONS
from llm import judge_answer_with_llm


def generate_eval_questions() -> None:
    from config import PROCESSED
    from llm import call_groq
    import re

    md_files = sorted(PROCESSED.glob("*.md"))
    if not md_files:
        raise SystemExit("[eval-gen] No processed markdown files found in processed/. Run extract first.")

    # Gather text content sample
    sample_text = ""
    for f in md_files:
        content = f.read_text()
        # Keep only basic text, avoid images/base64 to keep prompt clean
        clean = re.sub(r"!\[.*?\]\(.*?\)", "", content)
        clean = re.sub(r"data:image/.*?;base64,.*", "", clean)
        sample_text += clean[:10000] # Grab up to 10k chars per file
        if len(sample_text) > 30000:
            break

    sample_text = sample_text[:30000]

    system_prompt = (
        "You are an expert curriculum evaluator.\n"
        "Generate exactly 5 distinct, clear, factual questions about the provided source material.\n"
        "For each question, specify a list of 1 to 3 key phrases (must_contain) that are crucial "
        "to a correct answer. These must be literal, lowercase-insensitive substrings that appear in the context.\n"
        "Example output:\n"
        "[\n"
        "  {\n"
        "    \"q\": \"What is a projection matrix?\",\n"
        "    \"must_contain\": [\"projection\", \"column space\"]\n"
        "  }\n"
        "]\n"
        "Output ONLY valid JSON matching this schema. No markdown formatting blocks or surrounding text."
    )

    print("[eval-gen] Sending source content to Groq to generate evaluation questions...")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"SOURCE MATERIAL SAMPLE:\n{sample_text}"}
    ]

    from config import require_groq_key
    require_groq_key()

    try:
        response = call_groq(messages, max_tokens=1500, model="llama-3.3-70b-versatile")
        response = response.strip()
        # Strip any code block backticks if present
        if response.startswith("```"):
            lines = response.splitlines()
            if lines[0].startswith("```json") or lines[0].startswith("```"):
                lines = lines[1:-1]
            response = "\n".join(lines).strip()
        
        # Verify JSON
        parsed = json.loads(response)
        if not isinstance(parsed, list):
            raise ValueError("Expected a list of questions")
        
        # Save to EVAL_QUESTIONS
        EVAL_QUESTIONS.write_text(json.dumps(parsed, indent=2))
        print(f"[eval-gen] Successfully generated {len(parsed)} questions and saved to {EVAL_QUESTIONS}")
    except Exception as e:
        print(f"[eval-gen] Failed to generate questions: {e}")
        if 'response' in locals():
            print(f"[eval-gen] Raw API Response was:\n{response}")
        raise


def load_questions() -> list[dict]:
    if not EVAL_QUESTIONS.exists():
        raise SystemExit(
            f"No eval set at {EVAL_QUESTIONS}. Copy "
            "templates/eval_questions.example.json there and edit for your chapter."
        )
    return json.loads(EVAL_QUESTIONS.read_text())


def _hit(retrieved_text: str, must_contain: list[str]) -> bool:
    hay = retrieved_text.lower()
    return all(phrase.lower() in hay for phrase in must_contain)


def run(k: int = 5, judge: bool = False) -> dict:
    from generate import retrieve

    questions = load_questions()
    hits = 0
    rows = []
    
    # Infer subject from eval questions (math domain)
    subject = "mathematics"
    
    print(f"\n{'#':>2}  hit@{k}  question")
    print("-" * 70)
    for i, item in enumerate(questions, 1):
        q = item["q"]
        chunks, _ = retrieve(q, k, subject=subject)
        joined = "\n".join(c.get("text", "") for c in chunks)
        hit = _hit(joined, item.get("must_contain", []))
        hits += hit
        rows.append({"q": q, "hit": hit, "chunks": chunks})
        print(f"{i:>2}  {'✓' if hit else '✗':>5}  {q[:56]}")

        if judge:
            import time
            time.sleep(3.0)  # Avoid rate limits on free keys
            score = _judge_answer(q, joined)
            rows[-1]["judge"] = score
            print(f"      answer score: {score}/5")

    rate = hits / len(questions) if questions else 0.0
    print("-" * 70)
    print(f"retrieval hit@{k}: {hits}/{len(questions)} = {rate:.0%}")
    if judge:
        avg = sum(r.get("judge", 0) for r in rows) / len(rows)
        print(f"mean answer score: {avg:.2f}/5")
    return {"hit_rate": rate, "k": k, "rows": rows}


def _judge_answer(question: str, context: str) -> int:
    from generate import generate_spec  # noqa: F401  (kept for parity/imports)

    try:
        text = judge_answer_with_llm(question, context).strip()
        for tok in text.split():
            if tok.isdigit():
                return max(1, min(5, int(tok)))
    except Exception as e:
        print(f"[eval] judge grading failed: {e}")
    return 1


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--judge", action="store_true")
    a = ap.parse_args()
    run(k=a.k, judge=a.judge)
