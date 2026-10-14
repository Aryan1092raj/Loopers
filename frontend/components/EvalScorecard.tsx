"use client";
import { useState, type ReactNode } from "react";
import { HONESTY_TARGET, OVER_REFUSAL_LIMIT, targetMark, type Mark } from "@/lib/eval";
import type { EvalRun } from "@/lib/types";
import { CountUp } from "./Charts";

// stable formatters: CountUp restarts when its format changes
const pct1 = (v: number) => `${(v * 100).toFixed(1)}%`;
const signed3 = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(3)}`;
const pct = (v: unknown) => (typeof v === "number" ? pct1(v) : "not run");

const CHIP: Record<Mark, string> = {
  met: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  missed: "bg-red-50 text-red-700 ring-red-200",
  none: "bg-slate-100 text-slate-600 ring-slate-200",
};
const ICON: Record<Mark, string> = { met: "✓", missed: "✗", none: "–" };

/** A target check that never relies on colour alone: the symbol and the words carry the verdict. */
function Chip({ mark, children }: { mark: Mark; children: ReactNode }) {
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${CHIP[mark]}`}><span aria-hidden>{ICON[mark]}</span>{children}</span>;
}

function Card({ title, value, format, sentence, children }: { title: string; value: unknown; format: (v: number) => string; sentence: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <h3 className="text-base font-semibold text-slate-700">{title}</h3>
      {typeof value === "number"
        ? <CountUp value={value} format={format} className="block text-3xl font-semibold tabular-nums text-slate-900" />
        : <p className="text-3xl font-semibold text-slate-400">Not run</p>}
      <p className="text-xs leading-relaxed text-slate-600">{sentence}</p>
      {children && <div className="mt-auto flex flex-wrap gap-1.5 pt-1.5">{children}</div>}
    </div>
  );
}

/** Four headline answers for a judge: finds sources, answers faithfully, refuses honestly, personalizes. */
export function Scorecard({ run, judged, sim }: { run?: EvalRun; judged?: EvalRun; sim?: EvalRun }) {
  const k = (run?.config.k as number) ?? 5;
  const j = (judged ?? run)?.metrics ?? {};
  const d = sim?.metrics.paired_gain_difference;
  const honesty = targetMark(j.refusal_honesty, HONESTY_TARGET, "atLeast");
  const over = targetMark(j.over_refusal, OVER_REFUSAL_LIMIT, "atMost");
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <Card
        title="Finds the right source" value={run?.metrics[`hit@${k}`]} format={pct1}
        sentence={`The right page, slide or timestamp is in the top ${k} results${run ? `, on ${run.metrics.n_in_scope} questions` : ""}.`}
      />
      <Card
        title="Answers faithfully" value={j.faithfulness} format={pct1}
        sentence="How much of each answer is backed by the sources it retrieved."
      >
        <Chip mark="none">Citations point to the right source: {pct(j.citation_accuracy)}</Chip>
      </Card>
      <Card
        title="Refuses off-topic questions" value={j.refusal_honesty} format={pct1}
        sentence="Share of off-material questions the tutor declines instead of making something up."
      >
        <Chip mark={honesty}>Target at least {HONESTY_TARGET * 100}%</Chip>
        <Chip mark={over}>Wrongly refused {pct(j.over_refusal)}, target at most {OVER_REFUSAL_LIMIT * 100}%</Chip>
      </Card>
      <Card
        title="Personalization beats a fixed order" value={d?.mean} format={signed3}
        sentence={d ? `Extra mastery gained by adaptive question picking over a fixed order (± ${d.se.toFixed(3)}). Adaptive came out ahead for ${Math.round(d.students_where_adaptive_won * 100)}% of ${d.n} simulated students.` : "Adaptive question picking compared with a fixed order."}
      />
    </div>
  );
}

/** One collapsed block of detail. Its content mounts when opened, so charts animate when you look at them. */
export function Fold({ title, hint, children }: { title: string; hint: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)} className="group rounded-2xl border border-slate-200 bg-white shadow-sm">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-2xl px-4 py-3 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600 [&::-webkit-details-marker]:hidden">
        <span>
          <span className="block font-medium">{title}</span>
          <span className="block text-xs text-slate-500">{hint}</span>
        </span>
        <span aria-hidden className="text-xl leading-none text-slate-400 transition group-open:rotate-45">+</span>
      </summary>
      {open && <div className="border-t border-slate-100 p-4">{children}</div>}
    </details>
  );
}
