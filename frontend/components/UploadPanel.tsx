"use client";
import { useState } from "react";
import { ApiError, post } from "@/lib/api";
import { usePersisted } from "@/lib/usePersisted";
import { ErrorNote } from "./Note";
import UploadZone from "./UploadZone";

/** Upload a file or paste a video link; reports the started job to the parent. */
export default function UploadPanel({ onJob }: { onJob: (job: { id: string; title: string }) => void }) {
  const [kind, setKind] = usePersisted("loopers.upload.kind", "auto");
  const [lecture, setLecture] = usePersisted("loopers.upload.lecture", "");
  const [link, setLink] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function send(fd: FormData, title: string) {
    setError(null);
    setBusy(true);
    if (kind !== "auto") fd.append("kind", kind);
    if (lecture) fd.append("lecture_no", lecture);
    try {
      const r = await post<{ source_id: string; job_id: string }>("/api/sources", fd);
      onJob({ id: r.job_id, title });
      setLink("");
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

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1 text-slate-600">
          Kind
          <select value={kind} onChange={(e) => setKind(e.target.value)} className="rounded-lg border border-slate-200 bg-white px-2 py-1.5">
            <option value="auto">Detect from file</option>
            <option value="video">Lecture video</option>
            <option value="slides">Slide deck</option>
            <option value="textbook">Textbook</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-slate-600">
          Lecture no. (optional)
          <input type="number" min={1} value={lecture} onChange={(e) => setLecture(e.target.value)} className="w-24 rounded-lg border border-slate-200 bg-white px-2 py-1.5" />
        </label>
      </div>
      <UploadZone onFile={upload} disabled={busy} />
      <form onSubmit={(e) => { e.preventDefault(); addLink(); }} className="flex gap-2 text-sm">
        <label className="sr-only" htmlFor="video-link">Video link</label>
        <input
          id="video-link"
          type="url"
          required
          value={link}
          onChange={(e) => setLink(e.target.value)}
          placeholder="Or paste a YouTube link…"
          className="min-w-0 flex-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5"
        />
        <button type="submit" disabled={busy || !link.trim()} className="rounded-lg bg-indigo-600 px-3 py-1.5 text-white transition hover:bg-indigo-500 disabled:opacity-50">Add</button>
      </form>
      <ErrorNote message={error} />
    </div>
  );
}
