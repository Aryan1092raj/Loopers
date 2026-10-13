import { describe, expect, it } from "vitest";
import { splitAnswer } from "./answer";
import { bboxStyle, chipLabel, citationTarget } from "./citation";
import { HONESTY_TARGET, OVER_REFUSAL_LIMIT, targetMark } from "./eval";
import { faqFrom, mergeFaq } from "./faq";
import { layoutGraph, revealOrder } from "./layout";
import { buildPlan, horizonDays, startMastery } from "./plan";
import { masteryColor } from "./mastery";
import { parseOptions } from "./quiz";
import { topicLevel, topicsOf } from "./topics";
import type { Citation, GraphEdge, GraphNode } from "./types";

const cite = (id: string, over: Partial<Citation> = {}): Citation => ({
  id, unit_id: `u_${id}`, source_id: "s1", source_type: "video", title: "Lecture 04",
  locator: {}, excerpt: "...", ...over,
});
const node = (id: string): GraphNode => ({ id, name: id, level: "concept", parent_id: null, p_know: 0.15 });
const edge = (src: string, dst: string): GraphEdge => ({ src, dst, weight: 1 });

describe("splitAnswer", () => {
  it("turns markers into cite parts in order, including adjacent and repeated ones", () => {
    const c1 = cite("E1"), c2 = cite("E2");
    expect(splitAnswer("A [E1]. B [E2][E1]", [c1, c2])).toEqual([
      { kind: "text", text: "A " },
      { kind: "cite", citation: c1 },
      { kind: "text", text: ". B " },
      { kind: "cite", citation: c2 },
      { kind: "cite", citation: c1 },
    ]);
  });
  it("keeps a marker with no matching citation as plain text", () => {
    expect(splitAnswer("A [E9].", [cite("E1")])).toEqual([{ kind: "text", text: "A [E9]." }]);
  });
  it("returns nothing for an empty answer", () => {
    expect(splitAnswer("", [])).toEqual([]);
  });
});

describe("citationTarget", () => {
  it("gives a video seek and a page for a video unit matched to a slide", () => {
    const c = cite("E1", { locator: { t_start: 1425.2, matched_slide_page: 18 }, seek_url: "/media/video/lec04.mp4#t=1425.2", page_image_url: "/media/pages/s/18.webp" });
    expect(citationTarget(c)).toEqual({ video: { src: "/media/video/lec04.mp4", t: 1425.2 }, page: { url: "/media/pages/s/18.webp", bbox: null }, link: null });
  });
  it("links to the original for a URL source instead of playing a local file", () => {
    const c = cite("E1", { locator: { t_start: 83.9 }, seek_url: "https://www.youtube.com/watch?v=abc&t=83s" });
    expect(citationTarget(c)).toEqual({ video: null, page: null, link: "https://www.youtube.com/watch?v=abc&t=83s" });
  });
  it("gives only a page, with its bbox, for a textbook unit", () => {
    const c = cite("E1", { source_type: "textbook", locator: { page_no: 142, bbox: [1, 2, 3, 4] }, page_image_url: "/media/pages/s/142.webp" });
    expect(citationTarget(c)).toEqual({ video: null, page: { url: "/media/pages/s/142.webp", bbox: [1, 2, 3, 4] }, link: null });
  });
  it("gives nothing when the citation has no media urls", () => {
    expect(citationTarget(cite("E1", { locator: { page_no: 3 } }))).toEqual({ video: null, page: null, link: null });
  });
});

describe("bboxStyle", () => {
  it("converts page-image pixels to percentages", () => {
    expect(bboxStyle([100, 50, 300, 150], 1000, 500)).toEqual({ left: "10%", top: "10%", width: "20%", height: "20%" });
  });
  it("returns null while the image has no size", () => {
    expect(bboxStyle([1, 2, 3, 4], 0, 0)).toBeNull();
  });
});

describe("chipLabel", () => {
  it("formats timestamps, slides and pages", () => {
    expect(chipLabel(cite("E1", { locator: { t_start: 1425.2 } }))).toBe("Lecture 04 · 23:45");
    expect(chipLabel(cite("E1", { locator: { slide_no: 18 } }))).toBe("Lecture 04 · slide 18");
    expect(chipLabel(cite("E1", { locator: { page_no: 142 } }))).toBe("Lecture 04 · p. 142");
    expect(chipLabel(cite("E1"))).toBe("Lecture 04");
  });
});

describe("targetMark", () => {
  it("compares against the target in the right direction and says none when the metric was not run", () => {
    expect(targetMark(0.95, HONESTY_TARGET, "atLeast")).toBe("met");
    expect(targetMark(0.94, HONESTY_TARGET, "atLeast")).toBe("missed");
    expect(targetMark(0.05, OVER_REFUSAL_LIMIT, "atMost")).toBe("met");
    expect(targetMark(0.074, OVER_REFUSAL_LIMIT, "atMost")).toBe("missed");
    expect(targetMark(undefined, HONESTY_TARGET, "atLeast")).toBe("none");
  });
});

describe("buildPlan", () => {
  const today = new Date(2026, 9, 10); // 10 Oct 2026
  const ids = (days: ReturnType<typeof buildPlan>["days"]) => days.map((d) => d.tasks.map((t) => t.concept.id));
  const p = (v: Record<string, number>) => new Map(Object.entries(v));

  it("puts prerequisites first and never schedules a concept before the one it builds on", () => {
    const plan = buildPlan(["c", "b", "a"].map(node), [edge("a", "b"), edge("b", "c")], p({ a: 0.2, b: 0.2, c: 0.2 }), { examDate: "", minutes: 20, today });
    expect(ids(plan.days)).toEqual([["a"], ["b"], ["c"]]);
    expect(plan.days.map((d) => d.date)).toEqual(["2026-10-10", "2026-10-11", "2026-10-12"]);
  });
  it("keeps each day within the minutes the student set, 20 to learn and 10 to review", () => {
    const plan = buildPlan(["a", "b", "c"].map(node), [], p({ a: 0.2, b: 0.2, c: 0.7 }), { examDate: "", minutes: 30, today });
    expect(plan.days.every((d) => d.tasks.reduce((s, t) => s + t.minutes, 0) <= 30)).toBe(true);
    expect(plan.days.map((d) => d.tasks.map((t) => [t.concept.id, t.minutes]))).toEqual([[["a", 20]], [["b", 20], ["c", 10]]]); // b does not fit beside a
  });
  it("puts mastered concepts last as a final review", () => {
    const plan = buildPlan(["a", "b"].map(node), [], p({ a: 0.9, b: 0.2 }), { examDate: "", minutes: 60, today });
    expect(plan.days[0].tasks.map((t) => [t.concept.id, t.reason])).toEqual([["b", "mastery gap (20%)"], ["a", "final review"]]);
  });
  it("stops at the exam date and reports what did not fit", () => {
    const plan = buildPlan(["a", "b", "c"].map(node), [], p({ a: 0.2, b: 0.2, c: 0.2 }), { examDate: "2026-10-12", minutes: 20, today });
    expect(plan.days.map((d) => d.date)).toEqual(["2026-10-10", "2026-10-11"]);
    expect(plan.left.map((c) => c.id)).toEqual(["c"]);
  });
  it("plans a week without an exam date and at least one day for a past date", () => {
    expect(horizonDays("", today)).toBe(7);
    expect(horizonDays("2026-10-01", today)).toBe(1);
    expect(horizonDays("2030-01-01", today)).toBe(60);
  });
});

describe("startMastery", () => {
  it("uses the measured value for tested concepts and the topic's pick for the rest", () => {
    const topic = { node: node("t"), concepts: [node("a"), node("b")], mastery: 0 };
    const tested = { concept_id: "a", p_know: 0.3, retrievability: null, n_obs: 2, last_review: null };
    const untested = { concept_id: "b", p_know: 0.11, retrievability: null, n_obs: 0, last_review: null };
    expect([...startMastery([topic], { t: "strong" }, [tested, untested])]).toEqual([["a", 0.3], ["b", 0.8]]);
  });
});

describe("topicLevel", () => {
  const topic = { node: node("t"), concepts: [node("a"), node("b")], mastery: 0 };
  const row = (concept_id: string, p_know: number, n_obs = 1) => ({ concept_id, p_know, retrievability: null, n_obs, last_review: null });
  it("is new when no concept of the topic was tested", () => {
    expect(topicLevel(topic, [])).toEqual({ level: "new", p: null });
    expect(topicLevel(topic, [row("a", 0.9, 0), row("other", 0.9)])).toEqual({ level: "new", p: null });
  });
  it("averages only tested concepts and splits strong from shaky at 70%", () => {
    expect(topicLevel(topic, [row("a", 0.9), row("b", 0.5, 0)])).toEqual({ level: "strong", p: 0.9 });
    expect(topicLevel(topic, [row("a", 0.5), row("b", 0.7)])).toMatchObject({ level: "shaky" });
  });
});

describe("revealOrder", () => {
  it("lists every prerequisite before the concepts that need it, however the input is ordered", () => {
    const nodes = ["d", "c", "b", "a", "solo"].map(node);
    const edges = [edge("a", "c"), edge("b", "c"), edge("c", "d")];
    const order = revealOrder(nodes, edges).map((n) => n.id);
    for (const e of edges) expect(order.indexOf(e.src)).toBeLessThan(order.indexOf(e.dst));
    expect(order).toEqual(["a", "b", "solo", "c", "d"]);
  });
});

describe("layoutGraph", () => {
  it("puts each node one column right of its deepest prerequisite", () => {
    const placed = layoutGraph([node("a"), node("b"), node("c")], [edge("a", "b"), edge("b", "c")]);
    expect(placed.map((p) => [p.id, p.x, p.y])).toEqual([["a", 0, 0], ["b", 280, 0], ["c", 560, 0]]);
  });
  it("stacks nodes that share a column", () => {
    const placed = layoutGraph([node("a"), node("b"), node("c")], [edge("a", "c")]);
    expect(placed.map((p) => [p.id, p.x, p.y])).toEqual([["a", 0, 0], ["b", 0, 84], ["c", 280, 0]]);
  });
  it("wraps a tall column so the map stays wide, and the next layer starts after the wrap", () => {
    const solo = Array.from({ length: 9 }, (_, i) => node(`s${i}`));
    const placed = new Map(layoutGraph([node("a"), node("b"), ...solo], [edge("a", "b")]).map((p) => [p.id, p]));
    expect(placed.get("a")).toMatchObject({ x: 0, y: 0 });           // linked concepts sit at the top
    expect(Math.max(...[...placed.values()].map((p) => p.y))).toBe(6 * 84);
    expect(placed.get("s8")).toMatchObject({ x: 280, y: 2 * 84 });  // 10 nodes in layer 0 → two columns
    expect(placed.get("b")).toMatchObject({ x: 560, y: 0 });
  });
  it("ignores edges that point at nodes that are not in the list", () => {
    expect(layoutGraph([node("a")], [edge("ghost", "a")])).toEqual([{ id: "a", x: 0, y: 0 }]);
  });
  it("terminates on a cycle and on an empty graph", () => {
    expect(layoutGraph([node("a"), node("b")], [edge("a", "b"), edge("b", "a")])).toHaveLength(2);
    expect(layoutGraph([], [])).toEqual([]);
  });
});

describe("masteryColor", () => {
  it("runs red to green and clamps", () => {
    expect(masteryColor(0)).toBe("hsl(0 70% 85%)");
    expect(masteryColor(1)).toBe("hsl(120 70% 85%)");
    expect(masteryColor(1.7)).toBe("hsl(120 70% 85%)");
  });
});

describe("parseOptions", () => {
  it("parses a JSON array and survives bad input", () => {
    expect(parseOptions('["A","B"]')).toEqual(["A", "B"]);
    expect(parseOptions("not json")).toEqual([]);
    expect(parseOptions(null)).toEqual([]);
  });
});

describe("topicsOf", () => {
  it("collects concepts through subtopics and averages their mastery", () => {
    const n = (id: string, level: GraphNode["level"], parent_id: string | null, p_know = 0): GraphNode => ({ id, name: id, level, parent_id, p_know });
    const [t1, t2] = topicsOf({
      nodes: [n("T1", "topic", null), n("S1", "subtopic", "T1"), n("a", "concept", "S1", 0.2), n("b", "concept", "T1", 0.6), n("T2", "topic", null)],
      edges: [],
    });
    expect(t1.concepts.map((c) => c.id).sort()).toEqual(["a", "b"]);
    expect(t1.mastery).toBeCloseTo(0.4);
    expect(t2.concepts).toEqual([]);
    expect(t2.mastery).toBe(0);
  });
});

describe("faq", () => {
  const turn = (q: string, answer: string, mode = "grounded") => ({ q, r: { mode, answer } as never });

  it("keeps answered questions, drops refusals and citation markers", () => {
    const faq = faqFrom([turn("What is a limit?", "A limit is a value [E1] approached."), turn("Off topic", "", "refused"), { q: "pending" }]);
    expect(faq).toEqual([{ q: "What is a limit?", a: "A limit is a value approached." }]);
  });

  it("replaces a repeated question with the newer answer and moves it last", () => {
    const merged = mergeFaq([{ q: "A?", a: "old" }, { q: "B?", a: "b" }], [{ q: " a? ", a: "new" }]);
    expect(merged.map((m) => m.a)).toEqual(["b", "new"]);
  });
});
