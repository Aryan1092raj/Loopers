"use client";
import { animate, createDrawable, stagger } from "animejs";
import { useEffect, useRef } from "react";
import { reducedMotion } from "@/lib/motion";
import { Bars } from "./Charts";
import type { EvalRun } from "@/lib/types";

const pct = (v: unknown) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "not run");
const num = (v: unknown) => (typeof v === "number" ? v.toFixed(3) : "not run");

// ponytail: copied from `python -m evaluation.deepeval_check` (progress.md Run 18); store it as an eval run if it is rerun often.
const DEEPEVAL: Record<string, [number, number]> = {
  faithfulness: [1.0, 8], answer_relevancy: [0.971, 7], context_precision: [0.857, 6], context_recall: [1.0, 7],
};

function Table({ caption, rows }: { caption: string; rows: [string, string][] }) {
  return (
    <table className="w-full text-sm">
      <caption className="pb-1 text-left font-medium">{caption}</caption>
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k} className="border-t border-slate-200">
            <th scope="row" className="py-1 pr-4 text-left font-normal text-slate-600">{k}</th>
            <td className="py-1 text-right tabular-nums">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Retrieval quality of the newest golden-set run, by question type, plus the refusal gate calibration. */
export function RetrievalDetails({ run }: { run: EvalRun }) {
  const m = run.metrics;
  const k = (run.config.k as number) ?? 5;
  const types = Object.entries((m.by_type ?? {}) as Record<string, { n: number; mrr: number } & Record<string, number>>);
  return (
    <div className="space-y-5">
      <p className="text-base text-slate-700">
        {m.n_in_scope} in-scope and {m.n_off_material} off-material questions · run of {new Date(run.created_at).toLocaleString()}
      </p>
      {types.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-base font-semibold">Hit@{k} by question type</h3>
          <Bars rows={types.map(([t, v]) => ({ label: `${t.replace("_", " ")} (${v.n})`, value: v[`hit@${k}`], hint: `MRR ${num(v.mrr)}` }))} />
        </div>
      )}
      <div className="grid gap-6 md:grid-cols-2">
        <Table caption="All questions" rows={[[`Hit@${k}`, pct(m[`hit@${k}`])], ["MRR (rank of the first right source)", num(m.mrr)], ["Locator precision", pct(m.locator_precision)]]} />
        {m.calibration && (
          <Table caption="Refusal gate calibration" rows={[
            ["Threshold, low / high", `${m.calibration.tau_low} / ${m.calibration.tau_high}`],
            ["Honesty at the low threshold", pct(m.calibration.refusal_honesty)],
            ["Over-refusal at the low threshold", pct(m.calibration.over_refusal)],
            ["Targets met", m.calibration.targets_met ? "yes" : "no"]]} />
        )}
      </div>
    </div>
  );
}

const JUDGE_ROWS: [string, string][] = [
  ["faithfulness", "Faithfulness"], ["answer_relevancy", "Answer relevancy"], ["context_precision", "Context precision"], ["context_recall", "Context recall"],
];

/** The four judge metrics from our run next to the DeepEval cross-check, in one table. */
export function AnswerQualityDetails({ run }: { run: EvalRun }) {
  const m = run.metrics;
  return (
    <div className="space-y-4">
      <p className="text-base text-slate-700">
        Judge run of {new Date(run.created_at).toLocaleString()}. The cross-check recomputes the same four metrics with DeepEval on 10 saved answers,
        judged by Groq <code>gpt-oss-120b</code> on 12 Oct 2026; some calls returned no verdict, so each DeepEval mean covers 6 to 8 answers.
      </p>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
            <th scope="col" className="pb-1 font-medium">Metric</th>
            <th scope="col" className="pb-1 text-right font-medium">Our judge</th>
            <th scope="col" className="pb-1 text-right font-medium">DeepEval cross-check</th>
          </tr>
        </thead>
        <tbody>
          {JUDGE_ROWS.map(([key, label]) => (
            <tr key={key} className="border-t border-slate-200">
              <th scope="row" className="py-1 pr-4 text-left font-normal text-slate-600">{label}</th>
              <td className="py-1 text-right tabular-nums">{pct(m[key])}</td>
              <td className="py-1 text-right tabular-nums">{pct(DEEPEVAL[key][0])} <span className="text-xs text-slate-500">({DEEPEVAL[key][1]} answers)</span></td>
            </tr>
          ))}
          <tr className="border-t border-slate-200">
            <th scope="row" className="py-1 pr-4 text-left font-normal text-slate-600">Citation accuracy</th>
            <td className="py-1 text-right tabular-nums">{pct(m.citation_accuracy)}</td>
            <td className="py-1 text-right text-slate-400">not measured</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function Curves({ adaptive, control }: { adaptive: number[]; control: number[] }) {
  const w = 480, h = 160, pad = 28;
  const all = [...adaptive, ...control];
  const lo = Math.min(...all), hi = Math.max(...all);
  const x = (i: number) => pad + (i / (adaptive.length - 1)) * (w - 2 * pad);
  const y = (v: number) => h - pad - ((v - lo) / (hi - lo || 1)) * (h - 2 * pad);
  const line = (a: number[]) => a.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  const svgRef = useRef<SVGSVGElement>(null);
  // The two curves draw themselves left to right, then the session dots pop in.
  useEffect(() => {
    const el = svgRef.current;
    if (!el || reducedMotion()) return;
    // only the solid line draws: drawing rewrites stroke-dasharray, which would make the dashed control line solid
    const lines = animate(createDrawable(Array.from(el.querySelectorAll("path:not([stroke-dasharray])"))), { draw: ["0 0", "0 1"], duration: 1400, ease: "inOut(2)" });
    const control = animate(Array.from(el.querySelectorAll("path[stroke-dasharray]")), { opacity: [0, 1], duration: 900, delay: 300 });
    const dots = animate(Array.from(el.querySelectorAll("circle")), { opacity: [0, 1], scale: [0, 1], delay: stagger(40, { start: 900 }), duration: 300 });
    return () => { lines.revert(); control.revert(); dots.revert(); };
  }, [adaptive.length]);
  return (
    <svg ref={svgRef} role="img" aria-label="Mean true mastery by session, adaptive versus control" viewBox={`0 0 ${w} ${h}`} className="w-full max-w-xl">
      <path d={line(control)} fill="none" stroke="#64748b" strokeWidth="2" strokeDasharray="5 3" />
      <path d={line(adaptive)} fill="none" stroke="#4f46e5" strokeWidth="2.5" />
      {adaptive.map((v, i) => <circle key={i} cx={x(i)} cy={y(v)} r="2.5" fill="#4f46e5" style={{ transformBox: "fill-box", transformOrigin: "center" }} />)}
      <text x={pad} y={14} fontSize="11" fill="#4f46e5">adaptive</text>
      <text x={pad + 62} y={14} fontSize="11" fill="#64748b">control (dashed)</text>
      <text x={pad} y={h - 8} fontSize="10" fill="#64748b">session 0</text>
      <text x={w - pad} y={h - 8} fontSize="10" fill="#64748b" textAnchor="end">session {adaptive.length - 1}</text>
      <text x={4} y={y(hi) + 4} fontSize="10" fill="#64748b">{hi.toFixed(2)}</text>
      <text x={4} y={y(lo) + 4} fontSize="10" fill="#64748b">{lo.toFixed(2)}</text>
    </svg>
  );
}

/** Simulated students: the mastery curves, then gain per persona and question repetition, adaptive against control. */
export function SimulationDetails({ run }: { run: EvalRun }) {
  const m = run.metrics;
  const personas = Object.keys(m.adaptive.by_profile ?? {});
  return (
    <div className="space-y-5">
      <p className="text-base text-slate-700">
        {m.students_per_profile} students per persona × {personas.length || 3} personas, {m.sessions} sessions of {m.items_per_session} items · run of {new Date(run.created_at).toLocaleString()}
      </p>
      <Curves adaptive={m.adaptive.mean_curve} control={m.control.mean_curve} />
      <div className="grid gap-6 md:grid-cols-2">
        <div>
          <table className="w-full text-sm">
            <caption className="pb-1 text-left font-medium">Mean mastery gain by persona</caption>
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                <th scope="col" className="pb-1 font-medium">Persona</th>
                <th scope="col" className="pb-1 text-right font-medium">Adaptive</th>
                <th scope="col" className="pb-1 text-right font-medium">Control</th>
              </tr>
            </thead>
            <tbody>
              {personas.map((p) => (
                <tr key={p} className="border-t border-slate-200">
                  <th scope="row" className="py-1 pr-4 text-left font-normal capitalize text-slate-600">{p}</th>
                  <td className="py-1 text-right tabular-nums">{num(m.adaptive.by_profile[p])}</td>
                  <td className="py-1 text-right tabular-nums">{num(m.control.by_profile?.[p])}</td>
                </tr>
              ))}
              <tr className="border-t border-slate-300 font-medium">
                <th scope="row" className="py-1 pr-4 text-left">All</th>
                <td className="py-1 text-right tabular-nums">{num(m.adaptive.mean_gain)}</td>
                <td className="py-1 text-right tabular-nums">{num(m.control.mean_gain)}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <Table caption="Other measures" rows={[
          ["Questions repeated, adaptive", pct(m.adaptive.repetition_rate)], ["Questions repeated, control", pct(m.control.repetition_rate)],
          ["Mastery estimate error (BKT vs true)", num(m.adaptive.estimate_mae)]]} />
      </div>
    </div>
  );
}
