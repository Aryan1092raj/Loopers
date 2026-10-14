"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorNote } from "@/components/Note";
import { useStudent } from "@/lib/useStudent";

export default function ProfilePage() {
  const { ids, student, name, save } = useStudent();
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => setDraft(name ?? ""), [name]);

  if (!ids) return <p className="text-sm text-[#cdb3ea]">Loading…</p>;
  return (
    <div className="max-w-xl space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          setSaved(false);
          try { await save(draft); setSaved(true); }
          catch (err) { setError(err instanceof Error ? err.message : String(err)); }
        }}
        className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
      >
        <label className="flex flex-col gap-1 text-sm text-slate-600">
          Your name
          <input
            value={draft}
            onChange={(e) => { setDraft(e.target.value); setSaved(false); }}
            maxLength={60}
            required
            placeholder="What should we call you?"
            className="rounded-lg border border-slate-200 px-3 py-2 text-base text-slate-900"
          />
        </label>
        <div className="flex items-center gap-3">
          <button type="submit" disabled={!draft.trim() || draft.trim() === name} className="rounded-lg bg-indigo-600 px-4 py-1.5 text-white transition hover:bg-indigo-500 disabled:opacity-50">Save</button>
          {saved && <span role="status" className="text-sm text-green-700">Saved</span>}
        </div>
        <ErrorNote message={error} />
      </form>
      <dl className="grid grid-cols-[8rem_1fr] gap-y-2 rounded-2xl border border-slate-200 bg-white p-4 text-sm shadow-sm">
        <dt className="text-slate-500">Student ID</dt><dd className="break-all font-mono">{ids.studentId}</dd>
        <dt className="text-slate-500">Joined</dt><dd>{student ? new Date(student.created_at).toLocaleDateString() : "Save a name to create your profile"}</dd>
        <dt className="text-slate-500">Exam date</dt><dd>{student?.exam_date ?? "Not set"}</dd>
      </dl>
      <p className="text-sm text-[#d6b8f5]"><Link href="/dashboard" className="underline">Go to dashboard</Link></p>
    </div>
  );
}
