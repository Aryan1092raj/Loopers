"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import ChatPanel from "@/components/ChatPanel";
import ConceptDrawer from "@/components/ConceptDrawer";
import ConceptGraph from "@/components/ConceptGraph";
import JobCard from "@/components/JobCard";
import { Empty, ErrorNote } from "@/components/Note";
import SourcePanel from "@/components/SourcePanel";
import SourcesTable from "@/components/SourcesTable";
import TopicPicker from "@/components/TopicPicker";
import UploadPanel from "@/components/UploadPanel";
import { get } from "@/lib/api";
import { useIds } from "@/lib/session";
import { topicsOf } from "@/lib/topics";
import { useGet } from "@/lib/useGet";
import { usePersisted } from "@/lib/usePersisted";
import { useStudent } from "@/lib/useStudent";
import type { Citation, Graph } from "@/lib/types";

/** One collapsible block of the right-hand sidebar. Open state survives navigation. */
function Section({ id, title, hint, defaultOpen = true, children }: { id: string; title: string; hint?: string; defaultOpen?: boolean; children: React.ReactNode }) {
  const [open, setOpen] = usePersisted(`loopers.section.${id}`, defaultOpen);
  return (
    <details open={open} onToggle={(e) => setOpen(e.currentTarget.open)} className="group px-5 py-4">
      <summary className="flex cursor-pointer list-none items-start justify-between gap-3 rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600 [&::-webkit-details-marker]:hidden">
        <span>
          <span className="block font-semibold">{title}</span>
          {hint && <span className="block text-xs font-normal text-slate-500">{hint}</span>}
        </span>
        <span aria-hidden className="text-xl leading-none text-slate-400 transition group-open:rotate-45">+</span>
      </summary>
      <div className="mt-3 space-y-3">{children}</div>
    </details>
  );
}

function Workspace() {
  const ids = useIds();
  const { name } = useStudent();
  const cite = useSearchParams().get("cite");
  const graph = useGet<Graph>(ids ? `/api/graph?student_id=${encodeURIComponent(ids.studentId)}` : null);
  const [job, setJob] = usePersisted<{ id: string; title: string } | null>("loopers.upload.job", null);
  const [sourcesTick, setSourcesTick] = useState(0);
  const [active, setActive] = useState<{ c: Citation; n: number } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const open = (c: Citation) => setActive((a) => ({ c, n: (a?.n ?? 0) + 1 }));
  const names = useMemo(() => new Map((graph.data?.nodes ?? []).map((n) => [n.id, n.name])), [graph.data]);
  const topics = useMemo(() => (graph.data ? topicsOf(graph.data) : []), [graph.data]);
  const [topicId, setTopicId] = usePersisted<string | null>("loopers.chat.topic", null);
  const topic = topics.find((t) => t.node.id === topicId); // a topic that no longer exists falls back to all
  const scope = useMemo(
    () => (topic ? { topic_ids: [topic.node.id], concept_ids: topic.concepts.map((c) => c.id) } : { topic_ids: [], concept_ids: [] }),
    [topic],
  );

  useEffect(() => {
    if (!cite) return;
    get<Citation>(`/api/citations/${encodeURIComponent(cite)}`).then(open, () => setNote("That source is no longer available."));
  }, [cite]);

  // Escape closes the source drawer
  useEffect(() => {
    if (!active) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setActive(null); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [active]);

  if (!ids) return <p className="text-sm text-slate-500">Loading…</p>;
  const hasGraph = !!graph.data && graph.data.nodes.length > 0;
  return (
    <div className="space-y-3">
      <ErrorNote message={note} />
      <div className="grid gap-6 lg:h-[calc(100vh-5.5rem)] lg:grid-cols-[minmax(0,1fr)_26rem]">
        <ChatPanel
          studentId={ids.studentId}
          sessionId={ids.sessionId}
          onCite={open}
          name={name}
          scope={scope}
          toolbar={<TopicPicker topics={topics} value={topic?.node.id ?? null} onChange={setTopicId} />}
        />

        <aside aria-label="Course" className="min-h-0 min-w-0 overflow-x-hidden lg:overflow-y-auto">
          <div className="divide-y divide-slate-200 rounded-2xl border border-slate-200 bg-white shadow-sm">
            <Section id="material" title="Add course material" hint="PDFs, slides, lecture videos or a YouTube link">
              <UploadPanel onJob={(j) => { setJob(j); setSourcesTick((n) => n + 1); }} />
              {job && <JobCard key={job.id} jobId={job.id} title={job.title} onFinished={() => { setSourcesTick((n) => n + 1); graph.reload(); }} />}
              <h3 className="pt-1 text-sm font-medium text-slate-700">Uploaded files</h3>
              <SourcesTable refresh={sourcesTick} />
            </Section>

            <Section id="map" title="Course map" hint="Arrows run from a prerequisite to the concept that needs it. Colour is your mastery.">
              <ErrorNote message={graph.error} />
              {graph.loading && !graph.data && <p className="text-sm text-slate-500">Loading…</p>}
              {graph.data && !hasGraph && <Empty>No concepts yet. Add material and let the concepts stage finish.</Empty>}
              {hasGraph && graph.data && (
                <>
                  <ConceptGraph graph={graph.data} onSelect={setSelected} className="h-80" />
                  {selected && <ConceptDrawer key={selected} id={selected} names={names} onClose={() => setSelected(null)} />}
                </>
              )}
            </Section>

          </div>
        </aside>
      </div>

      {active && (
        <aside aria-label="Source viewer" className="fixed inset-y-0 right-0 z-30 flex w-full max-w-xl flex-col gap-3 overflow-y-auto border-l border-slate-200 bg-slate-50 p-4 shadow-2xl">
          <button type="button" onClick={() => setActive(null)} className="self-end rounded-lg border border-slate-200 bg-white px-3 py-1 text-sm hover:border-indigo-300">Close</button>
          <SourcePanel active={active} />
        </aside>
      )}
    </div>
  );
}

export default function HomePage() {
  return <Suspense fallback={null}><Workspace /></Suspense>;
}
