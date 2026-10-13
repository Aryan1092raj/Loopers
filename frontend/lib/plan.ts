import { revealOrder } from "./layout";
import type { Topic } from "./topics";
import type { GraphEdge, GraphNode, Mastery } from "./types";

/** Self-rated level of a topic, and the mastery it stands in for until a concept has been tested. */
export type Pick = "beginner" | "intermediate" | "strong";
export const START: Record<Pick, number> = { beginner: 0.2, intermediate: 0.5, strong: 0.8 };

const WEAK_BELOW = 0.6;    // learn: 20 minutes
const MASTERED_FROM = 0.8; // final review at the end of the plan: 10 minutes
const LEARN_MIN = 20;
const REVIEW_MIN = 10;
const NO_EXAM_DAYS = 7;
const MAX_DAYS = 60;

export interface PlanTask { concept: GraphNode; minutes: number; reason: string }
export interface PlanDay { date: string; tasks: PlanTask[] }

/** Starting mastery per concept of the chosen topics: a tested concept keeps its measured value, the rest take the topic's pick. */
export function startMastery(selected: Topic[], picks: Record<string, Pick>, mastery: Mastery[]): Map<string, number> {
  const tested = new Map(mastery.filter((m) => m.n_obs > 0 || m.last_review).map((m) => [m.concept_id, m.p_know]));
  const out = new Map<string, number>();
  for (const t of selected) for (const c of t.concepts) out.set(c.id, tested.get(c.id) ?? START[picks[t.node.id] ?? "beginner"]);
  return out;
}

/** The background the diagnostic starts from, taken from the average of the picks. */
export function backgroundOf(picks: Record<string, Pick>): "beginner" | "intermediate" | "advanced" {
  const v = Object.values(picks).map((p) => START[p]);
  const mean = v.length ? v.reduce((s, x) => s + x, 0) / v.length : START.beginner;
  return mean < 0.35 ? "beginner" : mean < 0.65 ? "intermediate" : "advanced";
}

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

/** Days available: until the exam date (at least 1, at most 60), or a week when there is no exam date. */
export function horizonDays(examDate: string, today: Date): number {
  if (!examDate) return NO_EXAM_DAYS;
  const exam = new Date(`${examDate}T00:00:00`);
  if (Number.isNaN(exam.getTime())) return NO_EXAM_DAYS;
  const start = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  return Math.max(1, Math.min(MAX_DAYS, Math.round((exam.getTime() - start.getTime()) / 86_400_000)));
}

/** Timetable for the chosen concepts. Prerequisites come first; days fill in order and never go back, so a
 *  concept is never scheduled before the one it builds on. Concepts that do not fit before the exam are returned in `left`. */
export function buildPlan(
  concepts: GraphNode[], edges: GraphEdge[], p: Map<string, number>,
  opts: { examDate: string; minutes: number; today: Date },
): { days: PlanDay[]; left: GraphNode[] } {
  const horizon = horizonDays(opts.examDate, opts.today);
  const ids = new Set(concepts.map((c) => c.id));
  const order = revealOrder(concepts, edges.filter((e) => ids.has(e.src) && ids.has(e.dst)));
  const mastery = (c: GraphNode) => p.get(c.id) ?? START.beginner;
  const main = order.filter((c) => mastery(c) < MASTERED_FROM);
  const final = order.filter((c) => mastery(c) >= MASTERED_FROM);

  const days: PlanDay[] = Array.from({ length: horizon }, (_, i) => {
    const d = new Date(opts.today.getFullYear(), opts.today.getMonth(), opts.today.getDate() + i);
    return { date: iso(d), tasks: [] };
  });
  const used = new Array<number>(horizon).fill(0);
  const left: GraphNode[] = [];
  let d = 0;
  for (const c of [...main, ...final]) {
    const m = mastery(c);
    const weak = m < WEAK_BELOW;
    const minutes = Math.min(weak ? LEARN_MIN : REVIEW_MIN, opts.minutes);
    while (d < horizon && used[d] + minutes > opts.minutes) d++;
    if (d >= horizon) { left.push(c); continue; }
    used[d] += minutes;
    const pct = Math.round(m * 100);
    days[d].tasks.push({ concept: c, minutes, reason: weak ? `mastery gap (${pct}%)` : m >= MASTERED_FROM ? "final review" : `review (${pct}%)` });
  }
  return { days: days.filter((x) => x.tasks.length > 0), left };
}
