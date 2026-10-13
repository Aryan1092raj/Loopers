"use client";
import { useEffect, useRef, useState } from "react";
import { get, post } from "@/lib/api";
import { parseOptions } from "@/lib/quiz";
import type { DiagnosticItem, DiagnosticReply } from "@/lib/types";
import { ErrorNote } from "./Note";

export const DIAGNOSTIC_QUESTIONS = 5;

/** The optional diagnostic: adaptive questions, `DIAGNOSTIC_QUESTIONS` of them. `onClose(true)` once the student
 *  has answered at least one question and is done (after the last one, on Finish, or when the bank runs out). */
export default function LevelTest({ studentId, background, examDate, onClose }: { studentId: string; background: string; examDate: string; onClose: (tested: boolean) => void }) {
  const id = encodeURIComponent(studentId);
  const [item, setItem] = useState<DiagnosticItem | null>(null);
  const [result, setResult] = useState<boolean | null>(null);
  const [answered, setAnswered] = useState(0);
  const [outOfQuestions, setOutOfQuestions] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setError(null);
    setBusy(true);
    try { return await fn(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  async function loadNext() {
    const r = await run(() => get<DiagnosticReply>(`/api/students/${id}/diagnostic`));
    if (!r) return;
    if ("status" in r) { setOutOfQuestions(true); setItem(null); return; }
    setItem(r);
    setResult(null);
  }

  // The student must exist before the first question; the background only sets where the adaptive test starts.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    run(() => post(`/api/students/onboard`, { student_id: studentId, background, exam_date: examDate || null })).then((ok) => { if (ok !== undefined) loadNext(); });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps -- once on open

  async function answer(option: string) {
    if (!item) return;
    const r = await run(() => post<{ correct: boolean }>(`/api/students/${id}/diagnostic/respond`, { item_id: item.item_id, response: [option] }));
    if (!r) return;
    setResult(r.correct);
    setAnswered((n) => n + 1);
  }

  return (
    <section aria-label="Diagnostic" className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-slate-500">Question {Math.min(answered + (result === null ? 1 : 0), DIAGNOSTIC_QUESTIONS)} of {DIAGNOSTIC_QUESTIONS}</p>
        <button type="button" onClick={() => onClose(answered > 0)} className="rounded-lg border border-slate-200 bg-white px-3 py-1 text-sm hover:border-indigo-300">{answered > 0 ? "Finish early" : "Cancel"}</button>
      </div>
      <ErrorNote message={error} />
      {busy && !item && <p className="text-sm text-slate-500">Loading a question…</p>}
      {outOfQuestions && <p className="text-sm text-slate-600">{answered === 0 ? "No test questions exist yet; they are added as quizzes are generated." : "That is every question we have for now."}</p>}
      {item && (
        <>
          <p className="font-medium">{item.stem}</p>
          <div className="grid gap-2">
            {parseOptions(item.options_json).map((o) => (
              <button key={o} type="button" disabled={busy || result !== null} onClick={() => answer(o)} className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-left hover:border-indigo-300 disabled:opacity-60">
                {o}
              </button>
            ))}
          </div>
          {result !== null && (
            <div className="flex items-center gap-3">
              <span className={`rounded px-2 py-0.5 text-xs ${result ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>{result ? "Correct" : "Not quite"}</span>
              {answered >= DIAGNOSTIC_QUESTIONS
                ? <button type="button" onClick={() => onClose(true)} className="rounded-lg bg-indigo-600 px-3 py-1 text-sm text-white hover:bg-indigo-500">See my schedule</button>
                : <button type="button" onClick={loadNext} disabled={busy} className="rounded-lg bg-indigo-600 px-3 py-1 text-sm text-white hover:bg-indigo-500">Next question</button>}
            </div>
          )}
        </>
      )}
    </section>
  );
}
