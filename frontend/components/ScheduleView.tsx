"use client";
import { useGet } from "@/lib/useGet";
import { usePersisted } from "@/lib/usePersisted";
import type { ScheduleDay } from "@/lib/types";
import { Empty, ErrorNote } from "./Note";

export default function ScheduleView({ studentId, names }: { studentId: string; names: Map<string, string> }) {
  const [minutes, setMinutes] = usePersisted("loopers.schedule.minutes", 60);
  const { data, error, loading } = useGet<ScheduleDay[]>(`/api/students/${encodeURIComponent(studentId)}/schedule?minutes_per_day=${minutes}`);
  return (
    <div className="space-y-2">
      <label className="flex items-center gap-2 text-sm">
        Minutes per day
        <select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} className="rounded border bg-white px-2 py-1">
          {[30, 60, 90].map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
      </label>
      <ErrorNote message={error} />
      {loading && !data && <p className="text-sm text-neutral-500">Loading…</p>}
      {data && data.every((d) => d.tasks.length === 0) && <Empty>Nothing scheduled yet. Take a quiz so the planner knows what to revisit.</Empty>}
      {data?.filter((d) => d.tasks.length > 0).map((d) => (
        <div key={d.date} className="rounded border bg-white p-3 text-sm">
          <h3 className="font-medium">{d.date}</h3>
          <ul className="mt-1 space-y-1">
            {d.tasks.map((t) => (
              <li key={t.concept_id}>{t.minutes} min · <strong>{names.get(t.concept_id) ?? t.concept_id}</strong> <span className="text-neutral-600">({t.reason})</span></li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
