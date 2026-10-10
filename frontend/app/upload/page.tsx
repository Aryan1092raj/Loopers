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
  const [link, setLink] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const finished = useCallback(() => setRefresh((n) => n + 1), []);

  function upload(file: File) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", file.name.replace(/\.[^.]+$/, ""));
    return send(fd, file.name);
  }

  function addLink() {
    const fd = new FormData();
    fd.append("url", link.trim());
    return send(fd, link.trim());
  }

  async function send(fd: FormData, title: string) {
    setError(null);
    setBusy(true);
    if (kind !== "auto") fd.append("kind", kind);
    if (lecture) fd.append("lecture_no", lecture);
    try {
      const r = await post<{ source_id: string; job_id: string }>("/api/sources", fd);
      setJob({ id: r.job_id, title });
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
      <form onSubmit={(e) => { e.preventDefault(); addLink(); }} className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex min-w-64 flex-1 flex-col gap-1">
          Or paste a video link (YouTube or similar)
          <input type="url" required value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://www.youtube.com/watch?v=…" className="rounded border bg-white px-2 py-1" />
        </label>
        <button type="submit" disabled={busy || !link.trim()} className="rounded bg-neutral-900 px-3 py-1 text-white disabled:opacity-50">Add link</button>
      </form>
      <p className="text-xs text-neutral-500">Linked videos are transcribed from their audio. Citations open the original video at the right second.</p>
      <ErrorNote message={error} />
      {job && <JobCard key={job.id} jobId={job.id} title={job.title} onFinished={finished} />}
      <h2 className="font-medium">Sources</h2>
      <SourcesTable refresh={refresh} />
    </div>
  );
}
