import Link from "next/link";
import { chipLabel } from "@/lib/citation";
import type { QuizReport } from "@/lib/types";
import { Bars, Pie, Stat, asPct } from "./Charts";
import { Empty } from "./Note";

export default function ReportView({ report, names }: { report: QuizReport; names: Map<string, string> }) {
  const label = (id: string) => names.get(id) ?? id;
  if (report.mastery_delta.length === 0) return <Empty>No answers were recorded for this quiz.</Empty>;
  const m = report.raw_metrics;
  const gained = report.mastery_delta.filter((d) => d.after > d.before).length;
  return (
    <div className="space-y-6">
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label="Score" value={report.score} format={asPct} sub={m ? `${m.correct_items} of ${m.total_items} correct` : undefined} />
        <Stat label="Concepts practised" value={report.mastery_delta.length} />
        <Stat label="Concepts improved" value={gained} sub={`of ${report.mastery_delta.length}`} />
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {m && (
          <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="font-medium">Answers</h2>
            <Pie parts={[
              { label: "Correct", value: m.correct_items, color: "#059669" },
              { label: "Wrong", value: m.total_items - m.correct_items, color: "#dc2626" },
            ]} />
          </section>
        )}
        <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="font-medium">Mastery change</h2>
          <p className="text-xs text-slate-500">The thin line marks mastery before the quiz.</p>
          <Bars rows={report.mastery_delta.map((d) => ({ label: label(d.concept_id), value: d.after, before: d.before }))} />
        </section>
      </div>

      {report.weak_topics.length > 0 && (
        <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="font-medium">Weak topics</h2>
          <Bars rows={report.weak_topics.map((t) => ({ label: label(t.topic_id), value: t.p_know }))} />
        </section>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        <section className="space-y-2 rounded-2xl border border-amber-200 bg-amber-50/60 p-4">
          <h2 className="font-medium text-amber-900">Likely misconceptions</h2>
          {report.misconceptions.length === 0 ? <p className="text-sm text-slate-600">None spotted in this quiz.</p> : (
            <ul className="space-y-2 text-sm">
              {report.misconceptions.map((x, i) => (
                <li key={i} className="rounded-lg bg-white p-2 shadow-sm">
                  <p>{x.label}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    {label(x.concept_id)}
                    {x.citation?.unit_id && <> · <Link href={`/?cite=${encodeURIComponent(x.citation.unit_id)}`} className="text-indigo-700 underline">{chipLabel(x.citation)}</Link></>}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>
        <section className="space-y-2 rounded-2xl border border-indigo-200 bg-indigo-50/60 p-4">
          <h2 className="font-medium text-indigo-900">What to study next</h2>
          {report.next_steps.length === 0 ? <p className="text-sm text-slate-600">Nothing to review right now.</p> : (
            <ul className="space-y-2 text-sm">
              {report.next_steps.map((s) => (
                <li key={s.concept_id} className="rounded-lg bg-white p-2 shadow-sm">
                  <strong>{label(s.concept_id)}</strong>
                  <p className="text-slate-600">{s.reason}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
