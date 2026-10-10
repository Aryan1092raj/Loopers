"use client";
import "@xyflow/react/dist/style.css";
import { Background, Controls, ReactFlow, type Edge, type Node } from "@xyflow/react";
import { useCallback, useMemo } from "react";
import { layoutGraph } from "@/lib/layout";
import { masteryColor } from "@/lib/mastery";
import type { Graph } from "@/lib/types";

export default function ConceptGraph({ graph, onSelect, className = "h-[70vh]" }: { graph: Graph; onSelect: (id: string) => void; className?: string }) {
  const { nodes, edges } = useMemo(() => {
    const concepts = graph.nodes.filter((n) => n.level !== "topic");
    const shown = concepts.length ? concepts : graph.nodes;
    const pos = new Map(layoutGraph(shown, graph.edges).map((p) => [p.id, p]));
    const ids = new Set(shown.map((n) => n.id));
    return {
      nodes: shown.map<Node>((n) => ({
        id: n.id,
        position: { x: pos.get(n.id)?.x ?? 0, y: pos.get(n.id)?.y ?? 0 },
        data: { label: `${n.name} · ${Math.round(n.p_know * 100)}%` },
        style: { background: masteryColor(n.p_know), border: "1px solid #888", borderRadius: 6, width: 220, fontSize: 12 },
      })),
      edges: graph.edges
        .filter((e) => ids.has(e.src) && ids.has(e.dst))
        .map<Edge>((e) => ({ id: `${e.src}->${e.dst}`, source: e.src, target: e.dst })),
    };
  }, [graph]);

  const onSelectionChange = useCallback(
    ({ nodes: sel }: { nodes: Node[] }) => { if (sel[0]) onSelect(sel[0].id); },
    [onSelect],
  );

  return (
    <div className={`${className} overflow-hidden rounded-xl border border-slate-200 bg-white`}>
      <ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false} nodesConnectable={false} onSelectionChange={onSelectionChange}>
        <Background />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
