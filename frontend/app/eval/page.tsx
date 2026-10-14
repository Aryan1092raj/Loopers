"use client";
import { useRef } from "react";
import { Empty, ErrorNote } from "@/components/Note";
import { Fold, Scorecard } from "@/components/EvalScorecard";
import { AnswerQualityDetails, RetrievalDetails, SimulationDetails } from "@/components/EvalTables";
import { useStaggerIn } from "@/lib/motion";
import { useGet } from "@/lib/useGet";
import type { EvalRun } from "@/lib/types";

const when = (r?: EvalRun) => (r ? new Date(r.created_at).toLocaleDateString() : "none yet");

export default function EvalPage() {
  const golden = useGet<EvalRun[]>("/api/eval/runs?kind=golden&limit=20");
  const sim = useGet<EvalRun[]>("/api/eval/runs?kind=simulation&limit=1");
  const run = golden.data?.[0];
  // the newest run made with --answers: retrieval-only runs leave the judge and refusal numbers empty
  const judged = golden.data?.find((r) => typeof r.metrics.faithfulness === "number");
  const simRun = sim.data?.[0];
  const root = useRef<HTMLDivElement>(null);
  useStaggerIn(root, !!golden.data && !!sim.data);
  return (
    <div ref={root} className="space-y-6">
      <p role="alert" className="rounded-lg border-2 border-red-500 bg-white px-4 py-2 text-sm font-semibold text-red-700 shadow-sm">
        Only for judges
      </p>
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Evaluation</h1>
        <p className="text-base text-slate-700">
          How well the tutor finds sources, answers, refuses and personalizes. Retrieval run {when(run)}, answer quality run {when(judged)}, simulation run {when(simRun)}.
        </p>
      </header>
      <ErrorNote message={golden.error ?? sim.error} />

      {(golden.loading || sim.loading) && !golden.data && <p className="text-sm text-[#cdb3ea]">Loading results…</p>}
      {(run || simRun) && <Scorecard run={run} judged={judged} sim={simRun} />}

      <div className="space-y-3">
        <Fold title="Retrieval" hint="Hit rate by question type, rank of the first right source, refusal gate calibration">
          {run ? <RetrievalDetails run={run} /> : <Empty>No golden-set run saved yet. Run <code>python -m evaluation.run --answers</code>.</Empty>}
        </Fold>
        <Fold title="Answer quality" hint="Faithfulness, relevancy, context precision and recall, with the DeepEval cross-check">
          {judged ? <AnswerQualityDetails run={judged} /> : <Empty>No judged run saved yet. Run <code>python -m evaluation.run --answers</code>.</Empty>}
        </Fold>
        <Fold title="Simulated students" hint="Mastery curves and gain per persona, adaptive against a fixed order">
          {simRun ? <SimulationDetails run={simRun} /> : <Empty>No simulation saved yet. Run <code>python -m evaluation.simulate</code>.</Empty>}
        </Fold>
      </div>

      <section aria-label="Caveats" className="space-y-1 rounded-2xl border border-indigo-200 bg-white/70 p-4 text-sm text-slate-600 shadow-sm">
        <h2 className="text-xl font-semibold text-slate-900">How to read these numbers</h2>
        <ul className="list-disc space-y-1 pl-5">
          <li>The golden questions were generated from the indexed sources by an LLM and have not all been checked by a person. Gold locators come from stored page, slide and timestamp fields.</li>
          <li>Judge metrics use a different model (Groq or Gemini) from the answer generator (DeepSeek), and are computed on a limited sample of answers.</li>
          <li>Simulated students follow an assumed learning model, so the gains show the effect of the question-picking policy under that model, not results with real learners.</li>
        </ul>
      </section>
    </div>
  );
}
