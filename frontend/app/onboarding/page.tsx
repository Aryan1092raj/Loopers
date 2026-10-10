"use client";
import Link from "next/link";
import { useState } from "react";
import { Empty, ErrorNote } from "@/components/Note";
import { get, post } from "@/lib/api";
import { parseOptions } from "@/lib/quiz";
import { useIds } from "@/lib/session";
import { usePersisted } from "@/lib/usePersisted";
import type { DiagnosticItem, DiagnosticReply } from "@/lib/types";

const LEVELS = [
  ["beginner", "New to this subject"],
  ["intermediate", "Some background"],
  ["advanced", "Comfortable with the basics"],
] as const;
const MAX_ITEMS = 5;

type Phase = "intake" | "diagnostic" | "done";

export default function OnboardingPage() {
  const ids = useIds();
  const [phase, setPhase] = usePersisted<Phase>("loopers.onboarding.phase", "intake");
  const [level, setLevel] = usePersisted<string>("loopers.onboarding.level", "beginner");
  const [item, setItem] = usePersisted<DiagnosticItem | null>("loopers.onboarding.item", null);
  const [answered, setAnswered] = usePersisted("loopers.onboarding.answered", 0);
  const [result, setResult] = usePersisted<boolean | null>("loopers.onboarding.result", null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setError(null);
    setBusy(true);
    try { return await fn(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function loadNext(count: number) {
    if (!ids) return;
    if (count >= MAX_ITEMS) { setPhase("done"); return; }
    const r = await run(() => get<DiagnosticReply>(`/api/students/${encodeURIComponent(ids.studentId)}/diagnostic`));
    if (!r) return;
    if ("status" in r) { setPhase("done"); return; }
    setItem(r);
    setResult(null);
  }

  async function start() {
    if (!ids) return;
    const ok = await run(() => post(`/api/students/onboard`, { student_id: ids.studentId, background: level }));
    if (ok === undefined) return;
    setPhase("diagnostic");
    await loadNext(0);
  }

  async function answer(option: string) {
    if (!ids || !item) return;
    const r = await run(() =>
      post<{ correct: boolean }>(`/api/students/${encodeURIComponent(ids.studentId)}/diagnostic/respond`, { item_id: item.item_id, response: [option] }),
    );
    if (!r) return;
    setResult(r.correct);
    setAnswered((n) => n + 1);
  }

  if (!ids) return <p className="text-sm text-neutral-500">Loading…</p>;
  return (
    <div className="mx-auto max-w-xl space-y-4">
      <h1 className="text-xl font-semibold">Welcome</h1>
      <ErrorNote message={error} />

      {phase === "intake" && (
        <form onSubmit={(e) => { e.preventDefault(); start(); }} className="space-y-3 rounded border bg-white p-4">
          <fieldset className="space-y-2">
            <legend className="font-medium">How familiar are you with this course?</legend>
            {LEVELS.map(([value, label]) => (
              <label key={value} className="flex items-center gap-2">
                <input type="radio" name="level" value={value} checked={level === value} onChange={() => setLevel(value)} />
                {label}
              </label>
            ))}
          </fieldset>
          <button type="submit" disabled={busy} className="rounded bg-neutral-900 px-3 py-1 text-white disabled:opacity-50">Start diagnostic</button>
        </form>
      )}

      {phase === "diagnostic" && item && (
        <section aria-label="Diagnostic question" className="space-y-3 rounded border bg-white p-4">
          <p className="text-xs text-neutral-500">Question {answered + (result === null ? 1 : 0)} of {MAX_ITEMS}</p>
          <p className="font-medium">{item.stem}</p>
          <div className="grid gap-2">
            {parseOptions(item.options_json).map((o) => (
              <button key={o} type="button" disabled={busy || result !== null} onClick={() => answer(o)} className="rounded border px-3 py-1 text-left hover:bg-neutral-100 disabled:opacity-60">
                {o}
              </button>
            ))}
          </div>
          {result !== null && (
            <div className="flex items-center gap-3">
              <span className={`rounded px-2 py-0.5 text-xs ${result ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>{result ? "Correct" : "Not quite"}</span>
              <button type="button" onClick={() => loadNext(answered)} className="rounded bg-neutral-900 px-3 py-1 text-white">Next</button>
            </div>
          )}
        </section>
      )}

      {phase === "done" && answered === 0 && (
        <Empty>
          No diagnostic questions exist yet; the item bank fills as quizzes are generated. Your starting level is saved.{" "}
          <Link href="/quiz" className="underline">Take a practice quiz</Link>.
        </Empty>
      )}
      {phase === "done" && answered > 0 && (
        <div className="space-y-2 rounded border bg-white p-4">
          <p className="font-medium">Diagnostic complete.</p>
          <Link href="/dashboard" className="underline">See your mastery</Link>
        </div>
      )}
    </div>
  );
}
