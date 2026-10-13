"use client";
import { useEffect, useState } from "react";
import { del } from "@/lib/api";
import { useGet } from "@/lib/useGet";
import type { SourceRow } from "@/lib/types";
import { Empty, ErrorNote } from "./Note";
import {
  Expandable,
  ExpandableContent,
  ExpandableTrigger,
} from "./ui/expandable";
import { cn } from "@/lib/utils";

const KIND_STYLES: Record<SourceRow["kind"], string> = {
  video: "bg-red-50 text-red-700 ring-red-200",
  slides: "bg-amber-50 text-amber-800 ring-amber-200",
  textbook: "bg-emerald-50 text-emerald-700 ring-emerald-200",
};

const STATUS_STYLES: Record<SourceRow["status"], string> = {
  queued: "bg-slate-100 text-slate-600",
  running: "bg-indigo-100 text-indigo-700",
  done: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

function formatDate(iso: string) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function SourceCard({
  s,
  onRemove,
}: {
  s: SourceRow;
  onRemove: (s: SourceRow) => void;
}) {
  return (
    <Expandable
      expandBehavior="push"
      transitionDuration={0.3}
      className="rounded-xl border border-slate-200 bg-white transition hover:border-slate-300"
    >
      {({ isExpanded }) => (
        <>
          <ExpandableTrigger aria-label={`${isExpanded ? "Collapse" : "Expand"} ${s.title}`}>
            <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-3 py-2.5 sm:grid-cols-[minmax(0,1fr)_7rem_5rem_1.5rem]">
              <p className="min-w-0 truncate text-sm font-medium text-slate-800" title={s.title}>
                {s.title}
              </p>
              <p className="hidden sm:block">
                <span
                  className={cn(
                    "inline-block rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset",
                    KIND_STYLES[s.kind] ?? "bg-slate-100 text-slate-600 ring-slate-200",
                  )}
                >
                  {s.kind}
                </span>
              </p>
              <p className="hidden text-sm tabular-nums text-slate-600 sm:block">
                {s.lecture_no != null ? `Lec ${s.lecture_no}` : "–"}
              </p>
              <p className="flex items-center gap-1.5 sm:hidden">
                <span
                  className={cn(
                    "inline-block rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset",
                    KIND_STYLES[s.kind] ?? "bg-slate-100 text-slate-600 ring-slate-200",
                  )}
                >
                  {s.kind}
                </span>
                <span className="text-xs tabular-nums text-slate-500">
                  {s.lecture_no != null ? `Lec ${s.lecture_no}` : ""}
                </span>
              </p>
              <span
                aria-hidden
                className={cn(
                  "justify-self-end text-lg leading-none text-slate-400 transition-transform duration-300",
                  isExpanded && "rotate-45",
                )}
              >
                +
              </span>
            </div>
          </ExpandableTrigger>
          <ExpandableContent preset="slide-up">
            <div className="space-y-2 border-t border-slate-100 px-3 py-3 text-sm">
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-4">
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">Status</p>
                  <p className="mt-0.5">
                    <span className={cn("inline-block rounded-full px-2 py-0.5 text-xs font-medium", STATUS_STYLES[s.status])}>
                      {s.status}
                    </span>
                  </p>
                </div>
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">Units</p>
                  <p className="mt-0.5 tabular-nums text-slate-700">{s.unit_count}</p>
                </div>
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">Language</p>
                  <p className="mt-0.5 text-slate-700">{s.lang}</p>
                </div>
                <div>
                  <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">Added</p>
                  <p className="mt-0.5 text-slate-700">{formatDate(s.created_at)}</p>
                </div>
              </div>
              <p className="truncate text-xs text-slate-500" title={s.file_name}>File: {s.file_name}</p>
              <div className="flex justify-end pt-1">
                <button type="button" onClick={() => onRemove(s)} aria-label={`Remove ${s.title}`} className="rounded px-2 py-1 text-xs text-red-600 hover:bg-red-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-600">
                  Remove
                </button>
              </div>
            </div>
          </ExpandableContent>
        </>
      )}
    </Expandable>
  );
}

export default function SourcesTable({ refresh, onRemoved }: { refresh: number; onRemoved?: () => void }) {
  const { data, error, loading, reload } = useGet<SourceRow[]>("/api/sources");
  const [removeError, setRemoveError] = useState<string | null>(null);
  useEffect(() => { if (refresh > 0) reload(); }, [refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  async function remove(s: SourceRow) {
    if (!window.confirm(`Remove "${s.title}"? It leaves the tutor, quizzes and the concept map.`)) return;
    try {
      await del(`/api/sources/${s.source_id}`);
      setRemoveError(null);
      reload();
      onRemoved?.();
    } catch (e) {
      setRemoveError(e instanceof Error ? e.message : String(e));
    }
  }

  if (error) return <ErrorNote message={error} />;
  if (loading && !data) return <p className="text-sm text-neutral-500">Loading sources…</p>;
  if (!data?.length) return <Empty>No sources yet. Drop a file above to build the knowledge base.</Empty>;
  return (
    <div className="space-y-2">
      {removeError && <ErrorNote message={removeError} />}
      <div aria-hidden className="hidden grid-cols-[minmax(0,1fr)_7rem_5rem_1.5rem] gap-3 px-3 text-[11px] font-medium uppercase tracking-wide text-slate-400 sm:grid">
        <span>Name</span>
        <span>Kind</span>
        <span>Lecture</span>
        <span />
      </div>
      {data.map((s) => (
        <SourceCard key={s.source_id} s={s} onRemove={remove} />
      ))}
    </div>
  );
}
