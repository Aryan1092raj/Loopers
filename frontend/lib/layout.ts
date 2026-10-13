import type { GraphEdge, GraphNode } from "./types";

const COL = 280;
const ROW = 84;
const MAX_ROWS = 7; // a layer taller than this wraps into extra columns, so the map stays wide rather than tall

/** Layer of each node = length of the longest prerequisite chain ending at it (0 = no prerequisites).
 *  Edges run src → dst, where src is a prerequisite of dst; edges to nodes outside the list are ignored. */
export function layerOf(nodes: GraphNode[], edges: GraphEdge[]): Map<string, number> {
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
  return depth;
}

/** Study order: by layer, then by name, so every prerequisite comes before the concepts that need it. */
export function revealOrder(nodes: GraphNode[], edges: GraphEdge[]): GraphNode[] {
  const layer = layerOf(nodes, edges);
  return [...nodes].sort((a, b) => (layer.get(a.id) ?? 0) - (layer.get(b.id) ?? 0) || a.name.localeCompare(b.name));
}

/** Layered left-to-right layout by `layerOf`. Linked concepts come first in each layer,
 *  so the chains sit at the top and standalone concepts fill the grid below and beside them. */
export function layoutGraph(nodes: GraphNode[], edges: GraphEdge[]): { id: string; x: number; y: number }[] {
  const ids = new Set(nodes.map((n) => n.id));
  const live = edges.filter((e) => ids.has(e.src) && ids.has(e.dst));
  const depth = layerOf(nodes, edges);
  const linked = new Set(live.flatMap((e) => [e.src, e.dst]));
  const layers = new Map<number, GraphNode[]>();
  for (const n of nodes) layers.set(depth.get(n.id) ?? 0, [...(layers.get(depth.get(n.id) ?? 0) ?? []), n]);

  const out: { id: string; x: number; y: number }[] = [];
  let x0 = 0;
  for (const d of [...layers.keys()].sort((a, b) => a - b)) {
    const layer = layers.get(d)!.sort((a, b) => Number(linked.has(b.id)) - Number(linked.has(a.id)) || a.name.localeCompare(b.name));
    layer.forEach((n, i) => out.push({ id: n.id, x: (x0 + Math.floor(i / MAX_ROWS)) * COL, y: (i % MAX_ROWS) * ROW }));
    x0 += Math.ceil(layer.length / MAX_ROWS);
  }
  return out;
}
