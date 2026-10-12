"use client";
import { useRef } from "react";
import { Empty, ErrorNote } from "@/components/Note";
import { Ring } from "@/components/Charts";
import { GoldenRun, SimulationRun } from "@/components/EvalTables";
import { useStaggerIn } from "@/lib/motion";
import { useGet } from "@/lib/useGet";
import type { EvalRun } from "@/lib/types";

// ponytail: copied from `python -m evaluation.deepeval_check` (progress.md Run 18); store it as an eval run if it is rerun often.
const DEEPEVAL: [string, number, number][] = [
  ["Faithfulness", 1.0, 8], ["Answer relevancy", 0.971, 7], ["Context precision", 0.857, 6], ["Context recall", 1.0, 7],
];

export default function EvalPage() {
  const golden = useGet<EvalRun[]>("/api/eval/runs?kind=golden&limit=20");
  const sim = useGet<EvalRun[]>("/api/eval/runs?kind=simulation&limit=1");
  const judged = golden.data?.find((r) => typeof r.metrics.faithfulness === "number");
  const root = useRef<HTMLDivElement>(null);
  useStaggerIn(root, !!golden.data && !!sim.data);
  return (
    <div ref={root} className="max-w-5xl space-y-8">
      <h1 className="text-xl font-semibold">Evaluation</h1>
      <ErrorNote message={golden.error ?? sim.error} />
      <section className="space-y-3">
        <h2 className="font-medium">Retrieval and answer quality</h2>
        {golden.data?.[0] ? <GoldenRun run={golden.data[0]} judged={judged} /> : !golden.loading && <Empty>No golden-set run saved yet. Run <code>python -m evaluation.run --answers</code>.</Empty>}
      </section>
      <section className="space-y-3">
        <h2 className="font-medium">Personalization with simulated students</h2>
        {sim.data?.[0] ? <SimulationRun run={sim.data[0]} /> : !sim.loading && <Empty>No simulation saved yet. Run <code>python -m evaluation.simulate</code>.</Empty>}
      </section>
      <section className="space-y-3">
        <h2 className="font-medium">DeepEval cross-check</h2>
        <p className="text-sm text-slate-600">
          The same four metrics computed by DeepEval (PS 5a) on 10 saved answers, judged by Groq <code>gpt-oss-120b</code> on 12 Oct 2026.
          Some metric calls returned no usable verdict, so each mean covers 6 to 8 answers.
        </p>
<div className="flex flex-wrap justify-around gap-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          {DEEPEVAL.map(([k, v, n]) => <Ring key={k} label={`${k} (${n} answers)`} value={v} color="#7c3aed" />)}
        </div>
      </section>
      <section aria-label="Caveats" className="space-y-1 text-sm text-slate-600">
        <h2 className="font-medium text-slate-900">How to read these numbers</h2>
        <ul className="list-disc space-y-1 pl-5">
          <li>The golden questions were generated from the indexed sources by an LLM and have not all been checked by a person. Gold locators come from stored page, slide and timestamp fields.</li>
          <li>Judge metrics use a different model (Groq or Gemini) from the answer generator (DeepSeek), and are computed on a limited sample of answers.</li>
          <li>Simulated students follow an assumed learning model, so the gains show the effect of the question-picking policy under that model, not results with real learners.</li>
        </ul>
      </section>
    </div>
  );
}
