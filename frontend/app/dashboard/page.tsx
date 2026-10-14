"use client";
import Link from "next/link";
import { useRef } from "react";
import { Bars, Stat, asPct } from "@/components/Charts";
import { Empty, ErrorNote } from "@/components/Note";
import MasteryHeatmap from "@/components/MasteryHeatmap";
import RevisionPanel from "@/components/RevisionPanel";
import { useStaggerIn } from "@/lib/motion";
import { useIds } from "@/lib/session";
import { topicsOf } from "@/lib/topics";
import { useGet } from "@/lib/useGet";
import { useStudent } from "@/lib/useStudent";
import type { Graph, Mastery } from "@/lib/types";

const card = "space-y-3 rounded-2xl border border-indigo-200 bg-white/70 p-4 shadow-sm";

export default function DashboardPage() {
  const ids = useIds();
  const { name } = useStudent();
  const sid = ids ? encodeURIComponent(ids.studentId) : null;
  const graph = useGet<Graph>(sid ? `/api/graph?student_id=${sid}` : null);
  const mastery = useGet<Mastery[]>(sid ? `/api/students/${sid}/mastery` : null);

  const concepts = (graph.data?.nodes ?? []).filter((n) => n.level !== "topic");
  const weak = [...concepts].sort((a, b) => a.p_know - b.p_know).filter((c) => c.p_know < 0.5);
  const touched = (mastery.data ?? []).length;
  const mean = concepts.length ? concepts.reduce((s, c) => s + c.p_know, 0) / concepts.length : 0;
  const topics = graph.data ? topicsOf(graph.data).filter((t) => t.concepts.length) : [];

  const root = useRef<HTMLDivElement>(null);
  useStaggerIn(root, !!graph.data);

  return (
    <div ref={root} className="space-y-5">
      <header className="space-y-0.5">
        <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
        <p className="text-base">{name ?? "Student"} · <span className="font-mono text-xs">{ids?.studentId ?? "…"}</span></p>
      </header>
      <ErrorNote message={graph.error ?? mastery.error} />
      {touched === 0 && graph.data && (
        <Empty>No activity yet. <Link href="/onboarding" className="underline">Plan your study</Link> or <Link href="/quiz" className="underline">take a quiz</Link> to start tracking mastery.</Empty>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Average mastery" value={mean} format={asPct} />
        <Stat label="Concepts tracked" value={touched} sub={`of ${concepts.length} in the course`} />
        <Stat label="Weak concepts" value={weak.length} sub="below 50%" />
        <Stat label="Topics" value={topics.length} />
      </div>

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <section aria-label="Mastery by topic" className={card}>
          <div>
            <h2 className="text-xl font-semibold">Mastery by topic</h2>
            <p className="text-sm text-slate-700">Every concept, coloured from red to green by how well you know it</p>
          </div>
          {graph.data ? <MasteryHeatmap graph={graph.data} mastery={mastery.data ?? []} /> : <p className="text-sm text-slate-500">Loading…</p>}
        </section>

        <div className="space-y-5 lg:sticky lg:top-20 lg:max-h-[calc(100vh-6.5rem)] lg:overflow-y-auto lg:pr-1">
          <section aria-label="Weak spots" className={card}>
            <div>
              <h2 className="text-xl font-semibold">Weak spots</h2>
              <p className="text-sm text-slate-700">Concepts below 50% and what to quiz next</p>
            </div>
            {weak.length === 0 ? <p className="text-base text-slate-700">Nothing below 50% yet.</p> : (
              <div className="space-y-4">
                <Bars rows={weak.slice(0, 6).map((c) => ({ label: c.name, value: c.p_know }))} />
                <div className="flex flex-wrap gap-2 text-sm">
                  {topics.filter((t) => t.mastery < 0.5).slice(0, 3).map((t) => (
                    <Link key={t.node.id} href={`/quiz?topic=${encodeURIComponent(t.node.id)}`} className="rounded-full bg-indigo-600 px-3 py-1.5 text-white">
                      Quiz on {t.node.name}
                    </Link>
                  ))}
                </div>
              </div>
            )}
          </section>
          <section aria-label="Revision" className={card}>
            <div>
              <h2 className="text-xl font-semibold">Revision</h2>
              <p className="text-sm text-slate-700">Flashcards and a 30-second brief for weak spots</p>
            </div>
            {ids && <RevisionPanel studentId={ids.studentId} />}
          </section>
        </div>
      </div>
    </div>
  );
}
