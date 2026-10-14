"use client";
import { useMemo, useState } from "react";
import LevelTest, { DIAGNOSTIC_QUESTIONS } from "@/components/LevelTest";
import { Empty, ErrorNote } from "@/components/Note";
import OnboardingTimeline from "@/components/OnboardingTimeline";
import { backgroundOf, buildPlan, startMastery, type Pick, type PlanDay } from "@/lib/plan";
import { useIds } from "@/lib/session";
import { topicLevel, topicsOf } from "@/lib/topics";
import type { Graph, Mastery, ScheduleDay } from "@/lib/types";
import { useGet } from "@/lib/useGet";
import { usePersisted } from "@/lib/usePersisted";

const PICKS: [Pick, string][] = [["beginner", "Beginner"], ["intermediate", "Intermediate"], ["strong", "Strong"]];
const MIN_MINUTES = 10;
const MAX_MINUTES = 480;

export default function OnboardingPage() {
  const ids = useIds();
  const [examDate, setExamDate] = usePersisted("loopers.onboarding.exam", "");
  const [minutesText, setMinutesText] = usePersisted("loopers.onboarding.minutes", ""); // empty until the student chooses
  const [picks, setPicks] = usePersisted<Record<string, Pick>>("loopers.onboarding.picks", {}); // a topic is ticked when it has a pick
  const [testing, setTesting] = useState(false);

  const id = ids ? encodeURIComponent(ids.studentId) : null;
  const graph = useGet<Graph>(id ? `/api/graph?student_id=${id}` : null);
  const mastery = useGet<Mastery[]>(id ? `/api/students/${id}/mastery` : null);

  const topics = useMemo(() => (graph.data ? topicsOf(graph.data).filter((t) => t.concepts.length) : []), [graph.data]);
  const selected = useMemo(() => topics.filter((t) => picks[t.node.id]), [topics, picks]);
  const minutes = Number(minutesText);
  const minutesOk = Number.isInteger(minutes) && minutes >= MIN_MINUTES && minutes <= MAX_MINUTES;

  // After the 5-question diagnostic the student can switch to the backend's schedule, which plans from the measured mastery.
  const [diagnosed, setDiagnosed] = usePersisted("loopers.onboarding.diagnosed", false);
  const [source, setSource] = usePersisted<"mine" | "backend">("loopers.onboarding.source", "mine");
  const schedule = useGet<ScheduleDay[]>(id && diagnosed && minutesOk ? `/api/students/${id}/schedule?minutes_per_day=${minutes}` : null);
  const backendDays = useMemo<PlanDay[]>(() => {
    if (!graph.data || !schedule.data) return [];
    const byId = new Map(graph.data.nodes.map((n) => [n.id, n]));
    const wanted = selected.length ? new Set(selected.flatMap((t) => t.concepts.map((c) => c.id))) : null; // no ticks: the whole course
    return schedule.data
      .map((d) => ({ date: d.date, tasks: d.tasks.flatMap((t) => (byId.has(t.concept_id) && (!wanted || wanted.has(t.concept_id)) ? [{ concept: byId.get(t.concept_id)!, minutes: t.minutes, reason: t.reason }] : [])) }))
      .filter((d) => d.tasks.length > 0);
  }, [graph.data, schedule.data, selected]);

  const plan = useMemo(() => {
    if (!graph.data || !minutesOk || !selected.length) return null;
    const concepts = selected.flatMap((t) => t.concepts);
    return buildPlan(concepts, graph.data.edges, startMastery(selected, picks, mastery.data ?? []), { examDate, minutes, today: new Date() });
  }, [graph.data, mastery.data, selected, picks, examDate, minutes, minutesOk]);

  function setPick(topicId: string, pick: Pick | null) {
    const next = { ...picks };
    if (pick) next[topicId] = pick; else delete next[topicId];
    setPicks(next);
  }

  if (!ids) return <p className="text-sm text-[#cdb3ea]">Loading…</p>;
  const card = "space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm";
  return (
    <div className="space-y-5">
      <header className="space-y-0.5">
        <h1 className="text-2xl font-semibold tracking-tight">Plan your study</h1>
        <p className="text-sm text-slate-600">Tell us your exam date, how long you can study each day and where you stand. Your timetable builds itself on the right.</p>
      </header>
      <ErrorNote message={graph.error ?? mastery.error} />

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <div className="space-y-4">
      <section aria-label="Exam and study time" className={card}>
        <div className="flex flex-wrap gap-4 text-sm">
          <label className="flex flex-col gap-1">
            Exam date (optional)
            <input type="date" value={examDate} onChange={(e) => setExamDate(e.target.value)} className="w-44 rounded-lg border border-slate-200 bg-white px-2 py-1.5" />
          </label>
          <label className="flex flex-col gap-1">
            Study time per day (minutes)
            <input
              type="number" inputMode="numeric" min={MIN_MINUTES} max={MAX_MINUTES} placeholder="for example 90"
              value={minutesText} onChange={(e) => setMinutesText(e.target.value)}
              aria-invalid={minutesText !== "" && !minutesOk}
              className="w-44 rounded-lg border border-slate-200 bg-white px-2 py-1.5"
            />
          </label>
        </div>
        {minutesText !== "" && !minutesOk && <p className="text-xs text-red-600">Enter a whole number from {MIN_MINUTES} to {MAX_MINUTES}.</p>}
      </section>

      <section aria-label="Topics" className={card}>
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 className="font-medium">Pick your topics and level</h2>
            <p className="text-xs text-slate-500">Tick what you want to study and rate yourself. Or take the diagnostic and get a schedule planned from your measured level.</p>
          </div>
          {!testing && <button type="button" onClick={() => setTesting(true)} className="rounded-xl bg-indigo-600 px-3.5 py-1.5 text-sm font-medium text-white">Take the {DIAGNOSTIC_QUESTIONS}-question diagnostic</button>}
        </div>
        {testing && (
          <LevelTest
            studentId={ids.studentId} background={backgroundOf(picks)} examDate={examDate}
            onClose={(tested) => { setTesting(false); mastery.reload(); if (tested) { setDiagnosed(true); setSource("backend"); } }}
          />
        )}
        {graph.loading && !graph.data && <p className="text-sm text-slate-500">Loading topics…</p>}
        {graph.data && topics.length === 0 && <Empty>No topics yet. Add course material and let the concepts stage finish.</Empty>}
        <ul className="space-y-2">
          {topics.map((t) => {
            const pick = picks[t.node.id];
            const measured = mastery.data ? topicLevel(t, mastery.data) : null;
            return (
              <li key={t.node.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 px-3 py-2">
                <label className="flex min-w-0 items-center gap-2 text-sm font-medium text-slate-800">
                  <input type="checkbox" checked={!!pick} onChange={(e) => setPick(t.node.id, e.target.checked ? "beginner" : null)} />
                  <span className="truncate">{t.node.name}</span>
                  <span className="font-normal text-slate-500">· {t.concepts.length} concepts</span>
                </label>
                <div className="flex flex-wrap items-center gap-3 text-sm">
                  {measured && measured.p !== null && (
                    <span className="text-xs text-slate-500">Measured: {measured.level === "strong" ? "Strong" : "Shaky"} · {Math.round(measured.p * 100)}%</span>
                  )}
                  {pick && (
                    <fieldset className="flex gap-3">
                      <legend className="sr-only">Your level in {t.node.name}</legend>
                      {PICKS.map(([value, label]) => (
                        <label key={value} className="flex items-center gap-1">
                          <input type="radio" name={`level-${t.node.id}`} checked={pick === value} onChange={() => setPick(t.node.id, value)} />
                          {label}
                        </label>
                      ))}
                    </fieldset>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </section>

      </div>

      <section aria-label="Your timetable" className={`${card} lg:sticky lg:top-20 lg:max-h-[calc(100vh-6.5rem)] lg:overflow-y-auto`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-medium">Your timetable</h2>
          {diagnosed && (
            <div role="group" aria-label="Timetable source" className="flex gap-1 text-sm">
              {([["mine", "From my levels"], ["backend", "From my diagnostic"]] as const).map(([value, label]) => (
                <button key={value} type="button" aria-pressed={source === value} onClick={() => setSource(value)}
                  className={`rounded-lg border px-3 py-1 ${source === value ? "border-indigo-500 bg-indigo-50 text-indigo-700" : "border-slate-200 bg-white hover:border-indigo-300"}`}>
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
        {diagnosed && source === "backend" ? (
          !minutesOk ? <Empty>Enter your study time per day to see your timetable.</Empty>
          : schedule.error ? <ErrorNote message={schedule.error} />
          : schedule.loading && !schedule.data ? <p className="text-sm text-slate-500">Building your schedule…</p>
          : <OnboardingTimeline days={backendDays} left={0} empty="Nothing is scheduled for these topics yet." />
        ) : (
          <>
            {!selected.length && <Empty>Tick at least one topic to see your timetable.</Empty>}
            {selected.length > 0 && !minutesOk && <Empty>Enter your study time per day to see your timetable.</Empty>}
            {plan && <OnboardingTimeline days={plan.days} left={plan.left.length} />}
          </>
        )}
      </section>
      </div>
    </div>
  );
}
