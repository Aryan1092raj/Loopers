"""Evaluation runner (TRD §8).

    cd RAG_DB/src
    python -m evaluation.run --k 5                      # retrieval metrics + gate calibration (no LLM needed)
    python -m evaluation.run --k 5 --write-thresholds   # also write metadata/gate_thresholds.json
    python -m evaluation.run --answers --limit 10       # + tutor answers, citation accuracy, refusal rates, judge metrics

Judge-metric definitions follow RAGAS (faithfulness, answer relevancy, context precision, context recall) but run on
our own judge calls (Gemini, then Groq), not the ragas package: the generator tries DeepSeek first, so the judge is a
different model (CLAUDE.md rule 7). Results are saved in the `eval_runs` table.
"""
import argparse
import json
import re
import uuid
from pathlib import Path
from datetime import datetime, timezone

import config
from core.db import get_connection
from evaluation import golden, metrics
from tutor import gate as gate_mod
from tutor.retrieve import retrieve_evidence

_LIST = re.compile(r"\[[\s\d.,]*\]")


def _judge(prompt: str) -> str:
    """Judge call on Gemini, else Groq; never the DeepSeek generator (CLAUDE.md rule 7). Both are free tiers with
    per-minute limits, so when both are rate limited the call waits and tries again instead of failing the run."""
    import time

    import llm

    msgs = [{"role": "user", "content": prompt}]
    calls = ([lambda: llm.call_gemini(msgs, max_tokens=300)] if config.GEMINI_KEYS else []) + [
        lambda: llm.call_groq(msgs, max_tokens=1500)]  # gpt-oss spends part of the budget on reasoning tokens
    for attempt in range(4):
        for call in calls:
            try:
                return call()
            except Exception as e:  # rate limit or empty reply: try the other judge
                err = e
        print(f"[eval] both judges failed ({err}); waiting {30 * (attempt + 1)} s")
        time.sleep(30 * (attempt + 1))
    raise err


def _scores(prompt: str, n: int) -> list[float]:
    """Ask the judge for a JSON list of n numbers in [0, 1]; [] when the reply does not parse to n numbers."""
    found = _LIST.search(_judge(prompt))
    try:
        vals = [float(v) for v in json.loads(found.group(0))] if found else []
    except ValueError:
        vals = []
    return [min(1.0, max(0.0, v)) for v in vals] if len(vals) == n else []


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def judge_metrics(question: str, answer: str, sentences: list[str], contexts: list[str], reference: str) -> dict:
    ctx = "\n\n".join(f"[{i}] {c[:800]}" for i, c in enumerate(contexts, 1))
    out = {}
    s = _scores(f"Context:\n{ctx}\n\nFor each numbered statement, output 1 if the context supports it, else 0. "
                "Reply with a JSON list only.\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(sentences, 1)), len(sentences))
    out["faithfulness"] = _mean(s)
    r = _scores(f"Question: {question}\nAnswer: {answer}\n\nRate from 0 to 1 how directly the answer addresses the question. "
                "Reply with a JSON list holding one number.", 1)
    out["answer_relevancy"] = _mean(r)
    p = _scores(f"Question: {question}\nReference answer: {reference}\n\nFor each numbered context, output 1 if it helps "
                f"answer the question, else 0. Reply with a JSON list only.\n{ctx}", len(contexts))
    if p:  # average precision over the ranked contexts
        hits = 0
        prec = []
        for i, v in enumerate(p, 1):
            hits += v >= 0.5
            if v >= 0.5:
                prec.append(hits / i)
        out["context_precision"] = _mean(prec) or 0.0
    ref = [t for t in re.split(r"(?<=[.!?])\s+", reference) if t]
    c = _scores(f"Context:\n{ctx}\n\nFor each numbered reference statement, output 1 if the context contains the information, "
                "else 0. Reply with a JSON list only.\n" + "\n".join(f"{i}. {t}" for i, t in enumerate(ref, 1)), len(ref))
    out["context_recall"] = _mean(c)
    return out


def indexed_sources() -> set[str]:
    """source_ids present in the active Qdrant collection."""
    import store

    c, ids, offset = store.client(), set(), None
    while True:
        points, offset = c.scroll(config.COLLECTION, limit=512, offset=offset, with_payload=["source_id"], with_vectors=False)
        ids |= {(p.payload or {}).get("source_id") for p in points}
        if offset is None:
            return ids


def _units(evidence) -> list[tuple[str, dict]]:
    return [(e.unit.source_id, e.unit.locator.model_dump()) for e in evidence if not e.is_prerequisite]


def answer_one(item: dict, evidence):
    """The /api/chat path minus session and mastery writes. Returns a TutorResponse."""
    from tutor import answer as ans

    g = gate_mod.decide(gate_mod.top_score(evidence))
    if g.band == "borderline":
        try:
            g = gate_mod.tier2(g, item["question"], evidence, ans.complete)
        except ans.NoProvider:
            pass
    if g.decision == "refuse":
        return ans._refused(g, "not_entailed" if g.nli else "out_of_material")
    return ans._grounded(item["question"], evidence, [], item["lang"], g)  # ponytail: private helper, public wrapper if reused


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--answers", action="store_true", help="generate answers and run citation, refusal and judge metrics")
    ap.add_argument("--limit", type=int, default=0, help="max items per class for --answers")
    ap.add_argument("--write-thresholds", action="store_true")
    ap.add_argument("--dump", help="with --answers, write question/answer/contexts/reference samples to this JSON file (for deepeval_check.py)")
    ap.add_argument("--indexed-only", action="store_true", help="skip items whose gold sources are not in the collection")
    a = ap.parse_args()

    items = golden.load()
    if a.indexed_only:  # before validation: items for sources that are not indexed are exactly the ones skipped
        idx = indexed_sources()
        kept = [it for it in items if all(g["source_id"] in idx for g in it["gold_locators"])]
        print(f"[eval] --indexed-only: {len(kept)} of {len(items)} items")
        items = kept
    problems = golden.validate(items)
    if problems:
        raise SystemExit("golden set invalid:\n" + "\n".join(problems))

    rows = []
    for it in items:
        evidence, reranked = retrieve_evidence(it["question"])
        if not reranked and any(e.unit.kind != "figure" and not e.is_prerequisite for e in evidence):  # text hits scored by dense similarity are on another scale; image-only hits have nothing to rerank
            raise SystemExit(f"cross-encoder reranking failed on {it['id']} (see the [generate] warning above); fix it and rerun")
        rows.append({"item": it, "evidence": evidence, "top": gate_mod.top_score(evidence)})
        print(f"[retrieve] {it['id']} top={rows[-1]['top']:.3f}")

    ins = [r for r in rows if r["item"]["type"] != "off_material"]
    offs = [r for r in rows if r["item"]["type"] == "off_material"]
    result = {"n_in_scope": len(ins), "n_off_material": len(offs)}
    ranks = [metrics.first_hit_rank(_units(r["evidence"][: a.k]), r["item"]["gold_locators"]) for r in ins]
    result.update(metrics.retrieval_metrics(ranks, a.k))
    result["by_type"] = {
        t: {"n": sum(r["item"]["type"] == t for r in ins),
            **metrics.retrieval_metrics([k for k, r in zip(ranks, ins) if r["item"]["type"] == t], a.k)}
        for t in sorted({r["item"]["type"] for r in ins})
    }
    pairs = [(r, s, l) for r in ins for s, l in _units(r["evidence"][: a.k])]
    result["locator_precision"] = sum(
        any(metrics.matches(s, l, g) for g in r["item"]["gold_locators"]) for r, s, l in pairs
    ) / max(1, len(pairs))

    if offs:
        cal = metrics.calibrate_gate([r["top"] for r in ins], [r["top"] for r in offs])
        result["calibration"] = cal
        if a.write_thresholds:
            path = gate_mod.Path(gate_mod.__file__).resolve().parents[2] / "metadata" / "gate_thresholds.json"
            path.write_text(json.dumps({"tau_low": cal["tau_low"], "tau_high": cal["tau_high"]}, indent=2))
            print(f"[eval] wrote {path}")

    if a.answers:
        sel_in = ins[: a.limit or None]
        sel_off = offs[: a.limit or None]
        cited, golds, in_ref, off_ref, judged, samples = [], [], [], [], [], []
        for r in sel_in + sel_off:
            it = r["item"]
            resp = answer_one(it, r["evidence"])
            refused = resp.mode == "refused"
            if it["type"] == "off_material":
                off_ref.append(refused)
                continue
            in_ref.append(refused)
            if refused:
                continue
            by_id = {e.unit.unit_id: e for e in r["evidence"]}
            cited.append([(c.source_id, c.locator.model_dump()) for c in resp.citations])
            golds.append(it["gold_locators"])
            used = {c.unit_id for c in resp.citations}
            ctxs = [e.unit.text for e in r["evidence"] if not e.is_prerequisite and e.unit.kind != "figure"][: a.k]
            judged.append(judge_metrics(it["question"], resp.answer, [s.text for s in resp.segments], ctxs, it["reference_answer"]))
            samples.append({"id": it["id"], "question": it["question"], "answer": resp.answer, "contexts": ctxs,
                            "reference": it["reference_answer"]})
            print(f"[answer] {it['id']} mode={resp.mode} cited={len(used & set(by_id))}")
        if a.dump:
            Path(a.dump).write_text(json.dumps(samples, ensure_ascii=False, indent=1))
            print(f"[eval] wrote {len(samples)} samples to {a.dump}")
        result["citation_accuracy"] = metrics.citation_accuracy(cited, golds)
        result.update(metrics.refusal_rates(in_ref, off_ref))
        for key in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
            result[key] = _mean([j[key] for j in judged if j.get(key) is not None])

    run_id = "ev_" + uuid.uuid4().hex[:10]
    conn = get_connection()
    try:
        conn.execute("INSERT INTO eval_runs(run_id, kind, config_json, metrics_json, created_at) VALUES (?,?,?,?,?)",
                     (run_id, "golden", json.dumps({"k": a.k, "answers": a.answers, "limit": a.limit}), json.dumps(result),
                      datetime.now(timezone.utc).isoformat()))
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"run_id": run_id, **result}, indent=2))


if __name__ == "__main__":
    main()
