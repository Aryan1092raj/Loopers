import type { Graph, GraphNode, Mastery } from "./types";

export interface Topic { node: GraphNode; concepts: GraphNode[]; mastery: number }

export type Level = "strong" | "shaky" | "new";
export const STRONG_FROM = 0.7;

/** Level from the topic's concepts the student has been tested on; "new" (p = null) when none were. */
export function topicLevel(t: Topic, mastery: Mastery[]): { level: Level; p: number | null } {
  const ids = new Set(t.concepts.map((c) => c.id));
  const tested = mastery.filter((m) => ids.has(m.concept_id) && (m.n_obs > 0 || m.last_review));
  if (!tested.length) return { level: "new", p: null };
  const p = tested.reduce((s, m) => s + m.p_know, 0) / tested.length;
  return { level: p >= STRONG_FROM ? "strong" : "shaky", p };
}

/** Each topic with the concept nodes under it (through any subtopics) and their mean mastery (0 when empty). */
export function topicsOf(g: Graph): Topic[] {
  const kids = new Map<string, GraphNode[]>();
  for (const n of g.nodes) if (n.parent_id) kids.set(n.parent_id, [...(kids.get(n.parent_id) ?? []), n]);
  const leaves = (id: string, seen = new Set<string>()): GraphNode[] =>
    seen.has(id) ? [] : (seen.add(id), (kids.get(id) ?? []).flatMap((k) => (k.level === "concept" ? [k] : leaves(k.id, seen))));
  return g.nodes
    .filter((n) => n.level === "topic")
    .map((node) => {
      const concepts = leaves(node.id);
      const mastery = concepts.length ? concepts.reduce((s, c) => s + c.p_know, 0) / concepts.length : 0;
      return { node, concepts, mastery };
    });
}
