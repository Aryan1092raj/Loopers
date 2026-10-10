"use client";
import Link from "next/link";
import { Empty, ErrorNote } from "@/components/Note";
import MasteryHeatmap from "@/components/MasteryHeatmap";
import ScheduleView from "@/components/ScheduleView";
import { useIds } from "@/lib/session";
import { useGet } from "@/lib/useGet";
import type { Graph, Mastery } from "@/lib/types";

export default function DashboardPage() {
  const ids = useIds();
  const sid = ids ? encodeURIComponent(ids.studentId) : null;
  const graph = useGet<Graph>(sid ? `/api/graph?student_id=${sid}` : null);
  const mastery = useGet<Mastery[]>(sid ? `/api/students/${sid}/mastery` : null);

  const concepts = (graph.data?.nodes ?? []).filter((n) => n.level !== "topic");
  const weak = [...concepts].sort((a, b) => a.p_know - b.p_know).filter((c) => c.p_know < 0.5).slice(0, 5);
  const touched = (mastery.data ?? []).length;

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Dashboard</h1>
      <p className="text-xs text-neutral-500">Student {ids?.studentId ?? "…"}</p>
      <ErrorNote message={graph.error ?? mastery.error} />
      {touched === 0 && graph.data && (
        <Empty>No activity yet. <Link href="/onboarding" className="underline">Do the diagnostic</Link> or <Link href="/quiz" className="underline">take a quiz</Link> to start tracking mastery.</Empty>
      )}

      <section aria-label="Mastery heatmap" className="space-y-2">
        <h2 className="font-medium">Mastery</h2>
        {graph.data ? <MasteryHeatmap graph={graph.data} mastery={mastery.data ?? []} /> : <p className="text-sm text-neutral-500">Loading…</p>}
      </section>

      <section aria-label="Weak concepts" className="space-y-2">
        <h2 className="font-medium">Weakest concepts</h2>
        {weak.length === 0 ? <p className="text-sm text-neutral-600">Nothing below 50% yet.</p> : (
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {weak.map((c) => <li key={c.id}>{c.name} · {Math.round(c.p_know * 100)}%</li>)}
          </ul>
        )}
      </section>

      {ids && (
        <section aria-label="Study schedule" className="space-y-2">
          <h2 className="font-medium">Study schedule</h2>
          <ScheduleView studentId={ids.studentId} names={new Map((graph.data?.nodes ?? []).map((n) => [n.id, n.name]))} />
        </section>
      )}
    </div>
  );
}
