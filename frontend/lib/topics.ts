import type { Graph, GraphNode } from "./types";

export interface Topic { node: GraphNode; concepts: GraphNode[]; mastery: number }

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
