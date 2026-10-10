import { describe, expect, it } from "vitest";
import { splitAnswer } from "./answer";
import { bboxStyle, chipLabel, citationTarget } from "./citation";
import { layoutGraph } from "./layout";
import { masteryColor } from "./mastery";
import { parseOptions } from "./quiz";
import { topicsOf } from "./topics";
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

describe("layoutGraph", () => {
  it("puts each node one column right of its deepest prerequisite", () => {
    const placed = layoutGraph([node("a"), node("b"), node("c")], [edge("a", "b"), edge("b", "c")]);
    expect(placed.map((p) => [p.id, p.x, p.y])).toEqual([["a", 0, 0], ["b", 280, 0], ["c", 560, 0]]);
  });
  it("stacks nodes that share a column", () => {
    const placed = layoutGraph([node("a"), node("b"), node("c")], [edge("a", "c")]);
    expect(placed.map((p) => [p.id, p.x, p.y])).toEqual([["a", 0, 0], ["b", 0, 84], ["c", 280, 0]]);
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
