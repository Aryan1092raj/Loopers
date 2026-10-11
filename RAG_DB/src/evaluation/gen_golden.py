"""Generate golden-set items from whatever is indexed, for any corpus (TRD §8, E1).

    cd RAG_DB/src
    python -m evaluation.gen_golden --per-source 8 --multihop 5 --hindi 5 --off 20

Writes metadata/golden_gen.jsonl. Questions and reference answers come from an LLM that reads one stored unit (or two
for multi-hop); the gold locator is copied from that unit's stored fields, never from the model (CLAUDE.md rule 1).
The judge LLM (Gemini, then Groq) writes the items, so the generator model is not grading its own questions.
A person must still open ~20 random gold locators before the set is trusted (PLAN E1).
"""
import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path

import config
import store
from evaluation import golden
from evaluation.run import _judge

MIN_CHARS = 300   # shorter units rarely carry a self-contained fact
MAX_CHARS = 1800  # prompt budget per unit
_OBJ = re.compile(r"\{.*\}", re.S)
_ARR = re.compile(r"\[.*\]", re.S)


def _json(prompt: str, pattern: re.Pattern):
    m = pattern.search(_judge(prompt))
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def load_units() -> dict[str, list[dict]]:
    """Text and transcript payloads in the active collection, grouped by source_id."""
    c = store.client()
    by_source: dict[str, list[dict]] = defaultdict(list)
    offset = None
    while True:
        points, offset = c.scroll(config.COLLECTION, limit=256, offset=offset, with_payload=True, with_vectors=False)
        for p in points:
            pl = p.payload or {}
            sid = pl.get("source_id")
            if sid and pl.get("kind") != "image" and len(pl.get("text") or "") >= MIN_CHARS:
                by_source[sid].append(pl)
        if offset is None:
            return by_source


def locator(pl: dict) -> dict:
    sid = pl["source_id"]
    if pl.get("t_start") is not None:
        return {"source_id": sid, "t_start": pl["t_start"], "t_end": pl.get("t_end")}
    if pl.get("slide_no") is not None:
        return {"source_id": sid, "slide_no": pl["slide_no"]}
    return {"source_id": sid, "page_no": pl["page_no"]}


def has_locator(pl: dict) -> bool:
    return any(pl.get(k) is not None for k in ("t_start", "slide_no", "page_no"))


def concepts_for(unit_ids: list[str]) -> list[str]:
    """Concept ids tagged on the units; [] before the concept build has run."""
    try:
        from core.db import get_connection
        conn = get_connection()
        try:
            rows = conn.execute(
                f"SELECT DISTINCT concept_id FROM unit_concepts WHERE unit_id IN ({','.join('?' * len(unit_ids))})", unit_ids
            ).fetchall()
        finally:
            conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def qa(units: list[dict]) -> dict | None:
    passages = "\n\n".join(f"Passage {i}:\n{u['text'][:MAX_CHARS]}" for i, u in enumerate(units, 1))
    need = "one question that needs information from every passage" if len(units) > 1 else "one question"
    out = _json(
        f"{passages}\n\nWrite {need} that a student could answer only from the passage(s), and a 1-2 sentence reference "
        "answer. The question must stand alone: no 'according to the passage'. "
        'Reply with JSON only: {"question": "...", "reference_answer": "..."}', _OBJ)
    return out if isinstance(out, dict) and out.get("question") and out.get("reference_answer") else None


def make_item(kind: str, n: int, units: list[dict], q: dict) -> dict:
    sid = units[0]["source_id"]
    if kind == "in_scope" and units[0].get("t_start") is not None:
        kind = "video"
    return {
        "id": f"g-{sid[:6]}-{kind}-{n:03d}", "type": kind, "lang": "en", "needs_figure": False,
        "question": q["question"].strip(), "reference_answer": q["reference_answer"].strip(),
        "gold_locators": [locator(u) for u in units],
        "gold_concepts": concepts_for([str(u.get("unit_id")) for u in units if u.get("unit_id")]),
        "expected_mode": "grounded",
    }


def to_hindi(item: dict, n: int) -> dict | None:
    out = _json(f"Translate into Hindi (Devanagari). Keep numbers and symbols.\nQuestion: {item['question']}\n"
                f"Answer: {item['reference_answer']}\n"
                'Reply with JSON only: {"question": "...", "reference_answer": "..."}', _OBJ)
    if not (isinstance(out, dict) and out.get("question") and out.get("reference_answer")):
        return None
    return {**item, "id": f"{item['id']}-hi", "type": "hindi", "lang": "hi",
            "question": out["question"].strip(), "reference_answer": out["reference_answer"].strip()}


def off_material(titles: list[str], n: int, start: int) -> list[dict]:
    out = _json(f"A study app holds these course sources:\n- " + "\n- ".join(titles) +
                f"\n\nWrite {n} factual student questions on subjects clearly unrelated to every source above, spread over "
                "different fields. Reply with a JSON list of strings only.", _ARR) or []
    return [{"id": f"g-off-{start + i:03d}", "type": "off_material", "lang": "en", "needs_figure": False, "question": q.strip(),
             "reference_answer": "", "gold_locators": [], "gold_concepts": [], "expected_mode": "refused"}
            for i, q in enumerate(out) if isinstance(q, str) and q.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-source", type=int, default=8, help="single-unit questions per source")
    ap.add_argument("--multihop", type=int, default=5, help="total two-unit questions")
    ap.add_argument("--hindi", type=int, default=5, help="Hindi translations of generated questions")
    ap.add_argument("--off", type=int, default=20, help="off-material questions")
    ap.add_argument("--out", default=str(golden.META / "golden_gen.jsonl"))
    a = ap.parse_args()

    rng = random.Random(0)
    by_source = load_units()
    by_source = {s: [u for u in us if has_locator(u)] for s, us in by_source.items()}
    seen = {i["question"].lower() for i in golden.load()}
    items: list[dict] = []

    def add(item: dict | None) -> bool:
        if item and item["question"].lower() not in seen:
            seen.add(item["question"].lower())
            items.append(item)
            print(f"[gen] {item['id']}: {item['question'][:70]}")
            return True
        return False

    for sid, units in by_source.items():
        n = 0
        for u in rng.sample(units, min(a.per_source, len(units))):
            q = qa([u])
            n += add(make_item("in_scope", n + 1, [u], q)) if q else 0

    multi = 0
    pool = [us for us in by_source.values() if len(us) >= 2]
    for _ in range(a.multihop * 3):  # retries: the model sometimes cannot link two random passages
        if multi >= a.multihop or not pool:
            break
        pair = rng.sample(rng.choice(pool), 2)
        q = qa(pair)
        multi += add(make_item("multi_hop", multi + 1, pair, q)) if q else 0

    singles = [i for i in items if i["type"] in ("in_scope", "video")]
    for n, item in enumerate(rng.sample(singles, min(a.hindi, len(singles))), 1):
        add(to_hindi(item, n))

    titles = sorted({u.get("title") or u.get("doc") or sid for sid, us in by_source.items() for u in us[:1]})
    items += off_material(titles, a.off, 1)

    Path(a.out).write_text("".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
    problems = golden.validate(golden.load())
    print(f"[gen] wrote {len(items)} items to {a.out}; golden.validate: {len(problems)} problems")
    print("\n".join(problems))


if __name__ == "__main__":
    main()
