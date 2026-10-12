import type { EvalRun } from "@/lib/types";

const pct = (v: unknown) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "not run");
const num = (v: unknown) => (typeof v === "number" ? v.toFixed(3) : "not run");

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

/** `judged` is the newest run made with --answers; retrieval-only runs leave the judge and refusal rows empty. */
export function GoldenRun({ run, judged }: { run: EvalRun; judged?: EvalRun }) {
  const m = run.metrics;
  const j = (judged ?? run).metrics;
  const k = (run.config.k as number) ?? 5;
  const types = Object.entries((m.by_type ?? {}) as Record<string, { n: number; mrr: number } & Record<string, number>>);
  return (
    <section aria-label="Golden set results" className="space-y-4">
      <p className="text-sm text-slate-600">
        {m.n_in_scope} in-scope and {m.n_off_material} off-material questions · {new Date(run.created_at).toLocaleString()}
      </p>
      <div className="grid gap-6 md:grid-cols-2">
        <Table caption="Retrieval" rows={[[`hit@${k}`, pct(m[`hit@${k}`])], ["MRR", num(m.mrr)], ["Locator precision", pct(m.locator_precision)]]} />
        <Table caption={judged && judged !== run ? `Judge metrics (run of ${new Date(judged.created_at).toLocaleString()})` : "Judge metrics"} rows={[
          ["Faithfulness", pct(j.faithfulness)], ["Answer relevancy", pct(j.answer_relevancy)],
          ["Context precision", pct(j.context_precision)], ["Context recall", pct(j.context_recall)],
          ["Citation accuracy", pct(j.citation_accuracy)]]} />
        <Table caption="Refusals" rows={[["Refusal honesty (off-material refused)", pct(j.refusal_honesty)], ["Over-refusal (in-scope refused)", pct(j.over_refusal)]]} />
        {m.calibration && (
          <Table caption="Gate calibration" rows={[
            ["tau low / high", `${m.calibration.tau_low} / ${m.calibration.tau_high}`],
            ["Honesty at tau low", pct(m.calibration.refusal_honesty)], ["Over-refusal at tau low", pct(m.calibration.over_refusal)],
            ["Targets met (honesty ≥ 95%, over-refusal ≤ 5%)", m.calibration.targets_met ? "yes" : "no"]]} />
        )}
      </div>
      {types.length > 0 && (
        <table className="w-full max-w-xl text-sm">
          <caption className="pb-1 text-left font-medium">Retrieval by question type</caption>
          <thead><tr className="text-left text-slate-600"><th scope="col">Type</th><th scope="col" className="text-right">Items</th><th scope="col" className="text-right">hit@{k}</th><th scope="col" className="text-right">MRR</th></tr></thead>
          <tbody>
            {types.map(([t, v]) => (
              <tr key={t} className="border-t border-slate-200">
                <th scope="row" className="py-1 text-left font-normal">{t.replace("_", " ")}</th>
                <td className="text-right tabular-nums">{v.n}</td><td className="text-right tabular-nums">{pct(v[`hit@${k}`])}</td><td className="text-right tabular-nums">{num(v.mrr)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Curves({ adaptive, control }: { adaptive: number[]; control: number[] }) {
  const w = 480, h = 160, pad = 28;
  const all = [...adaptive, ...control];
  const lo = Math.min(...all), hi = Math.max(...all);
  const x = (i: number) => pad + (i / (adaptive.length - 1)) * (w - 2 * pad);
  const y = (v: number) => h - pad - ((v - lo) / (hi - lo || 1)) * (h - 2 * pad);
  const line = (a: number[]) => a.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  return (
    <svg role="img" aria-label="Mean true mastery by session, adaptive versus control" viewBox={`0 0 ${w} ${h}`} className="w-full max-w-xl">
      <path d={line(control)} fill="none" stroke="#64748b" strokeWidth="2" strokeDasharray="5 3" />
      <path d={line(adaptive)} fill="none" stroke="#4f46e5" strokeWidth="2.5" />
      <text x={pad} y={14} fontSize="11" fill="#4f46e5">adaptive</text>
      <text x={pad + 62} y={14} fontSize="11" fill="#64748b">control (dashed)</text>
      <text x={pad} y={h - 8} fontSize="10" fill="#64748b">session 0</text>
      <text x={w - pad} y={h - 8} fontSize="10" fill="#64748b" textAnchor="end">session {adaptive.length - 1}</text>
      <text x={4} y={y(hi) + 4} fontSize="10" fill="#64748b">{hi.toFixed(2)}</text>
      <text x={4} y={y(lo) + 4} fontSize="10" fill="#64748b">{lo.toFixed(2)}</text>
    </svg>
  );
}

export function SimulationRun({ run }: { run: EvalRun }) {
  const m = run.metrics;
  const d = m.paired_gain_difference;
  return (
    <section aria-label="Simulated students" className="space-y-4">
      <p className="text-sm text-slate-600">
        {m.students_per_profile} students per profile × 3 profiles, {m.sessions} sessions of {m.items_per_session} items · {new Date(run.created_at).toLocaleString()}
      </p>
      <Curves adaptive={m.adaptive.mean_curve} control={m.control.mean_curve} />
      <div className="grid gap-6 md:grid-cols-2">
        <Table caption="Mean mastery gain" rows={[["Adaptive", num(m.adaptive.mean_gain)], ["Control", num(m.control.mean_gain)],
          ["Paired difference (± s.e.)", `${num(d.mean)} ± ${num(d.se)}`], ["Students where adaptive gained more", pct(d.students_where_adaptive_won)]]} />
        <Table caption="Question repetition" rows={[["Adaptive", pct(m.adaptive.repetition_rate)], ["Control", pct(m.control.repetition_rate)],
          ["BKT estimate error vs true mastery", num(m.adaptive.estimate_mae)]]} />
      </div>
    </section>
  );
}
