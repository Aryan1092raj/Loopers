"use client";
import { Empty, ErrorNote } from "@/components/Note";
import { GoldenRun, SimulationRun } from "@/components/EvalTables";
import { useGet } from "@/lib/useGet";
import type { EvalRun } from "@/lib/types";

export default function EvalPage() {
  const golden = useGet<EvalRun[]>("/api/eval/runs?kind=golden&limit=1");
  const sim = useGet<EvalRun[]>("/api/eval/runs?kind=simulation&limit=1");
  return (
    <div className="max-w-5xl space-y-8">
      <h1 className="text-xl font-semibold">Evaluation</h1>
      <ErrorNote message={golden.error ?? sim.error} />
      <section className="space-y-3">
        <h2 className="font-medium">Retrieval and answer quality</h2>
        {golden.data?.[0] ? <GoldenRun run={golden.data[0]} /> : !golden.loading && <Empty>No golden-set run saved yet. Run <code>python -m evaluation.run --answers</code>.</Empty>}
      </section>
      <section className="space-y-3">
        <h2 className="font-medium">Personalization with simulated students</h2>
        {sim.data?.[0] ? <SimulationRun run={sim.data[0]} /> : !sim.loading && <Empty>No simulation saved yet. Run <code>python -m evaluation.simulate</code>.</Empty>}
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
