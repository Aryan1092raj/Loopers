import type { GraphEdge, GraphNode } from "./types";

const COL = 280;
const ROW = 84;

/** Layered left-to-right layout: column = length of the longest prerequisite chain ending at the node.
 *  Edges run src → dst, where src is a prerequisite of dst. */
export function layoutGraph(nodes: GraphNode[], edges: GraphEdge[]): { id: string; x: number; y: number }[] {
  const ids = new Set(nodes.map((n) => n.id));
  const live = edges.filter((e) => ids.has(e.src) && ids.has(e.dst));
  const depth = new Map(nodes.map((n) => [n.id, 0]));
  // ponytail: bounded relaxation, so a stray cycle from the backend cannot hang the page
  for (let pass = 0; pass < nodes.length; pass++) {
    let changed = false;
    for (const e of live) {
      const d = (depth.get(e.src) ?? 0) + 1;
      if (d > (depth.get(e.dst) ?? 0)) { depth.set(e.dst, d); changed = true; }
    }
    if (!changed) break;
  }
  const used = new Map<number, number>();
  return nodes.map((n) => {
    const col = depth.get(n.id) ?? 0;
    const row = used.get(col) ?? 0;
    used.set(col, row + 1);
    return { id: n.id, x: col * COL, y: row * ROW };
  });
}
