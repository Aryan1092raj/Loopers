"use client";
import { useCallback, useState } from "react";
import JobCard from "@/components/JobCard";
import { ErrorNote } from "@/components/Note";
import SourcesTable from "@/components/SourcesTable";
import UploadZone from "@/components/UploadZone";
import { ApiError, post } from "@/lib/api";
import { usePersisted } from "@/lib/usePersisted";

export default function UploadPage() {
  const [job, setJob] = usePersisted<{ id: string; title: string } | null>("loopers.upload.job", null);
  const [kind, setKind] = usePersisted("loopers.upload.kind", "auto");
  const [lecture, setLecture] = usePersisted("loopers.upload.lecture", "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const finished = useCallback(() => setRefresh((n) => n + 1), []);

  async function upload(file: File) {
    setError(null);
    setBusy(true);
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", file.name.replace(/\.[^.]+$/, ""));
    if (kind !== "auto") fd.append("kind", kind);
    if (lecture) fd.append("lecture_no", lecture);
    try {
      const r = await post<{ source_id: string; job_id: string }>("/api/sources", fd);
      setJob({ id: r.job_id, title: file.name });
      setRefresh((n) => n + 1);
    } catch (e) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "Another ingestion job is running. Wait for it to finish, then upload again."
          : e instanceof Error ? e.message : String(e),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Upload course material</h1>
      <div className="flex flex-wrap items-end gap-4 text-sm">
        <label className="flex flex-col gap-1">
          Kind
          <select value={kind} onChange={(e) => setKind(e.target.value)} className="rounded border bg-white px-2 py-1">
            <option value="auto">Detect from file</option>
            <option value="video">Lecture video</option>
            <option value="slides">Slide deck</option>
            <option value="textbook">Textbook</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Lecture number (optional)
          <input type="number" min={1} value={lecture} onChange={(e) => setLecture(e.target.value)} className="w-24 rounded border bg-white px-2 py-1" />
        </label>
      </div>
      <UploadZone onFile={upload} disabled={busy} />
      <ErrorNote message={error} />
      {job && <JobCard key={job.id} jobId={job.id} title={job.title} onFinished={finished} />}
      <h2 className="font-medium">Sources</h2>
      <SourcesTable refresh={refresh} />
    </div>
  );
}
