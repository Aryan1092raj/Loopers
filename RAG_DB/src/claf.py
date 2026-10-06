"""Cognitive-Level Alignment Framework (CLAF).

Tags chunks with pedagogical metadata (subject_domain, grade_level,
cognitive_demand) using a lightweight LLM classifier.  During retrieval,
boosts chunks that match the learner's profile.  During generation,
selects the appropriate Adaptive Language Style Optimization (ALSO) prompt.

ALSO tiers (Bloom's Taxonomy aligned):
  basic        (Forms 1-2) — concrete, everyday analogies, ≤2-syllable terms
  intermediate (Forms 3-4) — core concepts, logical reasoning chains
  advanced     (undergrad) — full jargon, LaTeX derivations, abstract syntax
"""
import json

BLOOM_LEVELS = [
    "remember",
    "understand",
    "apply",
    "analyze",
    "evaluate",
    "synthesize",
]

GRADE_LEVELS = ["basic", "intermediate", "advanced"]

# Metadata boost weights for curriculum-aware reranking
_ALPHA_SUBJECT = 0.3   # boost for subject_domain match
_ALPHA_GRADE = 0.2     # boost for grade_level match
_ALPHA_LANG = 0.1      # boost for language match


_TAG_SYSTEM = """You are an educational metadata classifier. Given a text chunk from a course textbook, output JSON with:
{
  "subject_domain": "<one of: mathematics, physics, chemistry, biology, history, economics, geography, computer_science, literature, other>",
  "grade_level": "<one of: basic, intermediate, advanced>",
  "cognitive_demand": "<one of: remember, understand, apply, analyze, evaluate, synthesize>",
  "primary_language": "<iso 639-1 code, e.g. en, hi>"
}
Use grade_level=basic for class 1-8 material, intermediate for class 9-12, advanced for undergraduate+.
Return ONLY the JSON object."""


def tag_chunk(chunk_text: str, subject_hint: str = "") -> dict:
    """Call an LLM to classify a chunk's pedagogical metadata.

    Returns dict with keys: subject_domain, grade_level, cognitive_demand, primary_language.
    Falls back to safe defaults on any error.
    """
    try:
        from llm import call_gemini, call_groq
        hint = f"\nHint: this material is likely about {subject_hint}." if subject_hint else ""
        prompt = f"Classify this educational chunk:{hint}\n\n{chunk_text[:1500]}"
        messages = [
            {"role": "system", "content": _TAG_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = call_gemini(messages, model="gemini-3.8-flash", max_tokens=256)
        except Exception:
            raw = call_groq(messages, max_tokens=256)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1].removeprefix("json").strip()
        return json.loads(raw)
    except Exception:
        return {
            "subject_domain": subject_hint or "other",
            "grade_level": "intermediate",
            "cognitive_demand": "understand",
            "primary_language": "en",
        }


def rerank(chunks: list[dict], learner_profile: dict) -> list[dict]:
    """Boost chunk scores based on metadata match with the learner profile.

    learner_profile keys:
      grade_level    — "basic" | "intermediate" | "advanced"
      subject_domain — e.g. "physics" (optional)
      language       — iso 639-1 code, e.g. "en" (optional)

    Each matching field adds its alpha weight to the score.  Chunks are
    returned sorted descending by boosted score.
    """
    target_grade = learner_profile.get("grade_level", "")
    target_subject = learner_profile.get("subject_domain", "")
    target_lang = learner_profile.get("language", "en")

    boosted = []
    for chunk in chunks:
        boost = 0.0
        if target_grade and chunk.get("grade_level") == target_grade:
            boost += _ALPHA_GRADE
        if target_subject and chunk.get("subject_domain") == target_subject:
            boost += _ALPHA_SUBJECT
        if target_lang and chunk.get("primary_language") == target_lang:
            boost += _ALPHA_LANG
        score = chunk.get("score", 0.0) + boost
        boosted.append({**chunk, "score": score})

    return sorted(boosted, key=lambda x: x["score"], reverse=True)


_ALSO_PROMPTS = {
    "basic": """\
You are a teacher writing presentation slides for students in grades 1-8 (Forms 1-2).
RULES:
- Use only concrete, everyday language. Avoid passive voice and heavy nouns.
- Limit technical words to two syllables or explain them immediately with a simple analogy.
- Each bullet point must be a short, complete sentence a 10-year-old can follow.
- Use real-world examples (everyday objects, simple stories) to illustrate abstract ideas.
- Do NOT use jargon, Latin terms, or professional notation without explanation.
- Focus on Bloom's levels: remembering and understanding.""",

    "intermediate": """\
You are a teacher writing presentation slides for students in grades 9-12 (Forms 3-4).
RULES:
- Introduce core technical vocabulary, but provide a brief definition on first use.
- Build logical cause-and-effect chains: "Because X, therefore Y."
- Use general formulas when relevant, but label every variable.
- Balance concrete examples with conceptual explanations.
- Focus on Bloom's levels: applying and analyzing.""",

    "advanced": """\
You are a professor writing lecture slides for undergraduate or postgraduate students.
RULES:
- Use full domain-specific terminology without simplification.
- Include mathematical derivations in LaTeX notation (e.g. $\\\\int_a^b f(x)\\\\,dx$).
- Present abstract principles, edge cases, and formal proofs where relevant.
- Assume the audience has strong prerequisite knowledge.
- Focus on Bloom's levels: evaluating and synthesizing.""",
}


def build_also_prompt(grade_level: str, subject: str = "") -> str:
    """Return the ALSO system prompt for the given grade_level tier.

    grade_level: "basic" | "intermediate" | "advanced"
    subject: optional subject hint appended to prompt.
    """
    base = _ALSO_PROMPTS.get(grade_level, _ALSO_PROMPTS["intermediate"])
    if subject:
        base += f"\nThe subject area is: {subject}."
    return base
