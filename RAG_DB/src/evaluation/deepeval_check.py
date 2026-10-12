"""DeepEval cross-check of the runner's own judge metrics (PS 5a, PLAN E2).

Runs in its own venv, because deepeval pins packages that conflict with `transformers>=5`:

    python -m venv $TMPDIR/de && $TMPDIR/de/bin/pip install deepeval requests python-dotenv
    cd RAG_DB/src && python -m evaluation.run --answers --limit 10 --dump samples.json
    $TMPDIR/de/bin/python -m evaluation.deepeval_check samples.json 6   # first 6 samples

The judge is Groq (`openai/gpt-oss-120b`), a different model from the DeepSeek generator (CLAUDE.md rule 7). It is called
over HTTP here so this file needs neither the repo's `llm.py` nor torch. Prints the mean of each of the four metrics.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from deepeval.metrics import AnswerRelevancyMetric, ContextualPrecisionMetric, ContextualRecallMetric, FaithfulnessMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase
from dotenv import dotenv_values

ENV = {**dotenv_values(Path(__file__).resolve().parents[2] / ".env"), **os.environ}
KEYS = [k.strip() for k in (ENV.get("GROQ_API_KEY") or "").split(",") if k.strip()]
MODEL = ENV.get("GROQ_MODEL", "openai/gpt-oss-120b")
# DEEPEVAL_JUDGE=gemini judges on Gemini instead: Groq's per-minute token limit stalls DeepEval's long prompts.
GEMINI = ENV.get("DEEPEVAL_JUDGE") == "gemini"
GEMINI_MODEL = ENV.get("DEEPEVAL_GEMINI_MODEL", "gemini-3.5-flash")


def _gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    for attempt in range(8):
        r = requests.post(url, timeout=120, params={"key": ENV["GEMINI_API_KEY"]},
                          json={"contents": [{"parts": [{"text": prompt}]}]})
        if r.status_code == 200:
            parts = r.json()["candidates"][0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts)
        if r.status_code not in (429, 500, 503):
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:120]}")
        time.sleep(min(60, 5 * (attempt + 1)))
    return ""


class GroqJudge(DeepEvalBaseLLM):
    def __init__(self):
        self.n = 0

    def load_model(self):
        return MODEL

    def generate(self, prompt: str, schema=None):
        suffix = f"\n\nReply with JSON only, matching this schema: {json.dumps(schema.model_json_schema())}" if schema else ""
        text = _gemini(prompt + suffix) if GEMINI else ""
        for attempt in range(0 if GEMINI else 8):
            key = KEYS[self.n % len(KEYS)]
            self.n += 1
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=120,
                              headers={"Authorization": f"Bearer {key}"},
                              json={"model": MODEL, "max_tokens": 3000, "messages": [{"role": "user", "content": prompt + suffix}]})
            if r.status_code == 200:
                text = (r.json()["choices"][0]["message"].get("content") or "")
                if text:
                    break
            elif r.status_code == 429:
                time.sleep(min(60, float(r.headers.get("retry-after", 5 * (attempt + 1)))))
            else:
                raise RuntimeError(f"Groq {r.status_code}: {r.text[:120]}")
        if schema is None:
            return text
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise RuntimeError("judge returned no JSON after retries")
        return schema(**json.loads(m.group(0)))

    async def a_generate(self, prompt: str, schema=None):
        return self.generate(prompt, schema)

    def get_model_name(self):
        return f"gemini/{GEMINI_MODEL}" if GEMINI else f"groq/{MODEL}"


def main(path: str, n: int = 6) -> None:
    samples = json.loads(Path(path).read_text())[:n]
    judge = GroqJudge()
    names = {"faithfulness": FaithfulnessMetric, "answer_relevancy": AnswerRelevancyMetric,
             "context_precision": ContextualPrecisionMetric, "context_recall": ContextualRecallMetric}
    scores = {k: [] for k in names}
    for s in samples:
        case = LLMTestCase(input=s["question"], actual_output=s["answer"], expected_output=s["reference"],
                           retrieval_context=s["contexts"])
        for name, cls in names.items():
            try:
                m = cls(model=judge, async_mode=False, verbose_mode=False)
                m.measure(case)
                scores[name].append(m.score)
            except Exception as e:  # one malformed judge reply should not sink the run
                print(f"[deepeval] {s['id']} {name} failed: {str(e)[:100]}")
    out = {k: {"mean": sum(v) / len(v) if v else None, "n": len(v)} for k, v in scores.items()}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 6)
