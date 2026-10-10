import Link from "next/link";
import { chipLabel } from "@/lib/citation";
import type { QuizReport } from "@/lib/types";
import { Empty } from "./Note";

export default function ReportView({ report, names }: { report: QuizReport; names: Map<string, string> }) {
  const label = (id: string) => names.get(id) ?? id;
  if (report.mastery_delta.length === 0) return <Empty>No answers were recorded for this quiz.</Empty>;
  const m = report.raw_metrics;
  return (
    <div className="space-y-5">
      <p className="text-lg">
        Score <strong>{Math.round(report.score * 100)}%</strong>
        {m ? <span className="text-sm text-neutral-600"> · {m.correct_items} of {m.total_items} correct</span> : null}
      </p>

      <section>
        <h2 className="mb-1 font-medium">Mastery change</h2>
        <div className="overflow-x-auto rounded border bg-white">
          <table className="w-full text-left text-sm">
            <thead className="border-b bg-neutral-100"><tr><th className="p-2">Concept</th><th className="p-2">Before</th><th className="p-2">After</th></tr></thead>
            <tbody>
              {report.mastery_delta.map((d) => (
                <tr key={d.concept_id} className="border-b last:border-0">
                  <td className="p-2">{label(d.concept_id)}</td>
                  <td className="p-2">{Math.round(d.before * 100)}%</td>
                  <td className={`p-2 ${d.after >= d.before ? "text-green-700" : "text-red-700"}`}>{Math.round(d.after * 100)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {report.misconceptions.length > 0 && (
        <section>
          <h2 className="mb-1 font-medium">Likely misconceptions</h2>
          <ul className="space-y-1 text-sm">
            {report.misconceptions.map((x, i) => (
              <li key={i}>
                {x.label} <span className="text-neutral-500">({label(x.concept_id)})</span>{" "}
                {x.citation?.unit_id && <Link href={`/study?cite=${encodeURIComponent(x.citation.unit_id)}`} className="underline">{chipLabel(x.citation)}</Link>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h2 className="mb-1 font-medium">What to study next</h2>
        {report.next_steps.length === 0 ? <p className="text-sm text-neutral-600">Nothing to review right now.</p> : (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {report.next_steps.map((s) => <li key={s.concept_id}><strong>{label(s.concept_id)}</strong>: {s.reason}</li>)}
          </ul>
        )}
      </section>
    </div>
  );
}
