"use client";
import { useEffect, useState } from "react";
import { get, post } from "@/lib/api";
import { useJob } from "@/lib/useJob";
import { ErrorNote } from "./Note";

export default function JobCard({ jobId, title, onFinished }: { jobId: string; title: string; onFinished?: () => void }) {
  const { job, error } = useJob(jobId);
  const [log, setLog] = useState<string[]>([]);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const done = job?.status === "done";
  const failed = job?.status === "failed";

  useEffect(() => { if (done || failed) onFinished?.(); }, [done, failed, onFinished]);
  useEffect(() => {
    if (failed) get<{ lines: string[] }>(`/api/jobs/${jobId}/log?tail=40`).then((r) => setLog(r.lines), () => setLog([]));
  }, [failed, jobId]);

  const pct = Math.round((job?.progress ?? 0) * 100);
  return (
    <section aria-label={`Ingestion of ${title}`} className="min-w-0 space-y-2 rounded border bg-white p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="min-w-0 break-all font-medium">{title}</h2>
        <span className={`shrink-0 rounded px-2 py-0.5 text-xs ${done ? "bg-green-100 text-green-800" : failed ? "bg-red-100 text-red-800" : "bg-blue-100 text-blue-800"}`}>
          {job?.status ?? "starting"}
        </span>
      </div>
      <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} className="h-2 overflow-hidden rounded bg-neutral-200">
        <div className="h-full bg-blue-600 transition-all" style={{ width: `${pct}%` }} />
      </div>
      <p className="text-sm text-neutral-600">
        Stage: {job?.stage ?? "queued"} · {pct}%{job && job.units_created > 0 ? ` · ${job.units_created} units` : ""}
      </p>
      {job?.status === "running" && (
        <button
          type="button"
          onClick={() => {
            setCancelError(null);
            post(`/api/jobs/${jobId}/cancel`).catch((e) => setCancelError(e instanceof Error ? e.message : String(e)));
          }}
          className="text-sm underline"
        >
          Cancel job
        </button>
      )}
      <ErrorNote message={job?.error ?? error ?? cancelError} />
      {failed && log.length > 0 && <pre className="max-h-48 max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded bg-neutral-900 p-2 text-xs text-neutral-100">{log.join("\n")}</pre>}
    </section>
  );
}
