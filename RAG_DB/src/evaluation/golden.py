"""Golden set loader and validator (TRD §8). Reads metadata/golden_set.jsonl plus any metadata/golden_*.jsonl."""
import json
from pathlib import Path

META = Path(__file__).resolve().parents[2] / "metadata"
TYPES = {"in_scope", "off_material", "multi_hop", "video", "hindi"}
MODES = {"grounded", "partially_grounded", "refused", "outside_syllabus"}
REQUIRED = ("id", "type", "lang", "question", "reference_answer", "gold_locators", "expected_mode")


def load() -> list[dict]:
    items = []
    for path in sorted({META / "golden_set.jsonl", *META.glob("golden_*.jsonl")}):
        if path.is_file():
            items += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return items


def known_source_ids() -> set[str]:
    """Source ids are the first 16 hex chars of the file sha256 (see metadata/registry.json)."""
    registry = json.loads((META / "registry.json").read_text())
    return {v["sha256"][:16] for v in registry.values() if v.get("sha256")}


def validate(items: list[dict]) -> list[str]:
    """Problems found; an empty list means every item is well formed and every gold locator names a known source."""
    problems, seen, sources = [], set(), known_source_ids()
    for it in items:
        tag = it.get("id", "?")
        missing = [f for f in REQUIRED if f not in it]
        if missing:
            problems.append(f"{tag}: missing {missing}")
            continue
        if it["id"] in seen:
            problems.append(f"{tag}: duplicate id")
        seen.add(it["id"])
        if it["type"] not in TYPES or it["expected_mode"] not in MODES:
            problems.append(f"{tag}: bad type or expected_mode")
        if (it["type"] == "off_material") != (it["expected_mode"] == "refused"):
            problems.append(f"{tag}: off_material and refused must go together")
        if it["type"] != "off_material" and not it["gold_locators"]:
            problems.append(f"{tag}: no gold locator")
        problems += [f"{tag}: unknown source {g.get('source_id')}" for g in it["gold_locators"] if g.get("source_id") not in sources]
    return problems


def spotcheck(items: list[dict], n: int = 20, seed: int = 0) -> str:
    """n random in-scope items with their gold locators, for a person to open and confirm (PLAN E1)."""
    import random

    pool = [i for i in items if i["gold_locators"]]
    lines = []
    for it in random.Random(seed).sample(pool, min(n, len(pool))):
        where = "; ".join(f"{g['source_id']} " + (f"page {g['page_no']}" if g.get("page_no") is not None else
                          f"slide {g['slide_no']}" if g.get("slide_no") is not None else f"t={g.get('t_start')}s")
                          for g in it["gold_locators"])
        lines.append(f"{it['id']}  [{where}]\n  Q: {it['question']}\n  A: {it['reference_answer']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    data = load()
    errs = validate(data)
    print(f"{len(data)} items, {len(errs)} problems")
    print("\n".join(errs))
    if "--spotcheck" in sys.argv:
        print(spotcheck(data))
    raise SystemExit(bool(errs))
