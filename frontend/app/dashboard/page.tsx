"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Bars, Stat, asPct } from "@/components/Charts";
import { Empty, ErrorNote } from "@/components/Note";
import MasteryHeatmap from "@/components/MasteryHeatmap";
import RevisionPanel from "@/components/RevisionPanel";
import ScheduleView from "@/components/ScheduleView";
import { useStaggerIn } from "@/lib/motion";
import { useIds } from "@/lib/session";
import { topicsOf } from "@/lib/topics";
import { useGet } from "@/lib/useGet";
import { usePersisted } from "@/lib/usePersisted";
import { useStudent } from "@/lib/useStudent";
import type { Graph, Mastery } from "@/lib/types";

const SECTIONS = [
  ["mastery", "Mastery by topic", "Heatmap of every concept, grouped by topic"],
  ["weak", "Weak spots", "Concepts below 50% and what to quiz next"],
  ["revision", "Revision", "Flashcards and a 30-second brief for weak spots"],
  ["schedule", "Study schedule", "Daily plan from weak topics and forgetting"],
] as const;
type SectionId = (typeof SECTIONS)[number][0];

/** ☰ menu that picks the one dashboard section shown below it. */
function SectionMenu({ value, onChange }: { value: SectionId; onChange: (v: SectionId) => void }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", outside); document.removeEventListener("keydown", esc); };
  }, [open]);
  const current = SECTIONS.find((s) => s[0] === value)!;
  return (
    <div ref={root} className="relative z-30 w-fit">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm transition hover:border-indigo-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
      >
        <svg aria-hidden viewBox="0 0 20 20" className="h-4 w-4 text-slate-500"><path d="M3 5h14M3 10h14M3 15h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>
        <span className="font-medium">{current[1]}</span>
        <span aria-hidden className={`text-xs text-slate-400 transition ${open ? "rotate-180" : ""}`}>▾</span>
      </button>
      {open && (
        <ul role="menu" className="absolute left-0 z-40 mt-2 w-80 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 text-sm shadow-lg">
          {SECTIONS.map(([id, title, hint]) => (
            <li key={id} role="none">
              <button
                type="button"
                role="menuitemradio"
                aria-checked={id === value}
                onClick={() => { onChange(id); setOpen(false); }}
                className={`block w-full px-4 py-2 text-left hover:bg-indigo-50 focus-visible:bg-indigo-50 focus-visible:outline-none ${id === value ? "bg-indigo-50/60" : ""}`}
              >
                <span className={`block ${id === value ? "font-semibold text-indigo-700" : "text-slate-800"}`}>{title}</span>
                <span className="block text-xs text-slate-500">{hint}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const ids = useIds();
  const { name } = useStudent();
  const sid = ids ? encodeURIComponent(ids.studentId) : null;
  const graph = useGet<Graph>(sid ? `/api/graph?student_id=${sid}` : null);
  const mastery = useGet<Mastery[]>(sid ? `/api/students/${sid}/mastery` : null);
  const [section, setSection] = usePersisted<SectionId>("loopers.dashboard.section", "mastery");

  const concepts = (graph.data?.nodes ?? []).filter((n) => n.level !== "topic");
  const weak = [...concepts].sort((a, b) => a.p_know - b.p_know).filter((c) => c.p_know < 0.5);
  const touched = (mastery.data ?? []).length;
  const mean = concepts.length ? concepts.reduce((s, c) => s + c.p_know, 0) / concepts.length : 0;
  const topics = graph.data ? topicsOf(graph.data).filter((t) => t.concepts.length) : [];
  const names = new Map((graph.data?.nodes ?? []).map((n) => [n.id, n.name]));

  const root = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  useStaggerIn(root, !!graph.data);
  useStaggerIn(body, section);

  return (
    <div ref={root} className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">Dashboard</h1>
        <p className="text-xs text-slate-500">{name ?? "Student"} · <span className="font-mono">{ids?.studentId ?? "…"}</span></p>
      </header>
      <ErrorNote message={graph.error ?? mastery.error} />
      {touched === 0 && graph.data && (
        <Empty>No activity yet. <Link href="/onboarding" className="underline">Do the diagnostic</Link> or <Link href="/quiz" className="underline">take a quiz</Link> to start tracking mastery.</Empty>
      )}

      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label="Average mastery" value={mean} format={asPct} />
        <Stat label="Concepts tracked" value={touched} sub={`of ${concepts.length} in the course`} />
        <Stat label="Weak concepts" value={weak.length} sub="below 50%" />
        <Stat label="Topics" value={topics.length} />
      </div>

      <SectionMenu value={section} onChange={setSection} />

      <div ref={body} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        {section === "mastery" && (
          graph.data ? <MasteryHeatmap graph={graph.data} mastery={mastery.data ?? []} /> : <p className="text-sm text-slate-500">Loading…</p>
        )}
        {section === "weak" && (
          weak.length === 0 ? <p className="text-sm text-slate-600">Nothing below 50% yet.</p> : (
            <div className="space-y-4">
              <Bars rows={weak.slice(0, 10).map((c) => ({ label: c.name, value: c.p_know }))} />
              <div className="flex flex-wrap gap-2 text-sm">
                {topics.filter((t) => t.mastery < 0.5).slice(0, 4).map((t) => (
                  <Link key={t.node.id} href={`/quiz?topic=${encodeURIComponent(t.node.id)}`} className="rounded-full bg-indigo-600 px-3 py-1.5 text-white transition hover:bg-indigo-500">
                    Quiz on {t.node.name}
                  </Link>
                ))}
              </div>
            </div>
          )
        )}
        {section === "revision" && ids && <RevisionPanel studentId={ids.studentId} />}
        {section === "schedule" && ids && <ScheduleView studentId={ids.studentId} names={names} />}
      </div>
    </div>
  );
}
