"use client";
import "@xyflow/react/dist/style.css";
import { animate, stagger } from "animejs";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { layoutGraph } from "@/lib/layout";
import { masteryColor } from "@/lib/mastery";
import { reducedMotion } from "@/lib/motion";
import type { Graph } from "@/lib/types";

type ConceptData = { name: string; p: number };

/** One concept: name plus a mastery bar, so the number does not rely on colour alone. */
function ConceptNode({ data, selected }: NodeProps<Node<ConceptData>>) {
  const pct = Math.round(data.p * 100);
  return (
    <div
      className={`concept-node w-[220px] cursor-pointer rounded-xl border bg-white px-3 py-2 text-left shadow-sm transition-shadow hover:shadow-md ${selected ? "border-indigo-500 ring-2 ring-indigo-200" : "border-slate-200"}`}
      title={`${data.name}: mastery ${pct}%`}
    >
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-slate-300" />
      <p className="truncate text-xs font-medium text-slate-800">{data.name}</p>
      <div className="mt-1.5 flex items-center gap-2">
        <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
          <span className="block h-full rounded-full" style={{ width: `${Math.max(4, pct)}%`, background: masteryColor(data.p).replace("85%", "55%") }} />
        </span>
        <span className="text-[11px] tabular-nums text-slate-500">{pct}%</span>
      </div>
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-0 !bg-slate-300" />
    </div>
  );
}

const nodeTypes = { concept: ConceptNode };

export default function ConceptGraph({ graph, onSelect, className = "h-[70vh]" }: { graph: Graph; onSelect: (id: string) => void; className?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const { nodes, edges } = useMemo(() => {
    const concepts = graph.nodes.filter((n) => n.level !== "topic");
    const shown = concepts.length ? concepts : graph.nodes;
    const pos = new Map(layoutGraph(shown, graph.edges).map((p) => [p.id, p]));
    const ids = new Set(shown.map((n) => n.id));
    return {
      nodes: shown.map<Node<ConceptData>>((n) => ({
        id: n.id,
        type: "concept",
        position: { x: pos.get(n.id)?.x ?? 0, y: pos.get(n.id)?.y ?? 0 },
        data: { name: n.name, p: n.p_know },
      })),
      edges: graph.edges
        .filter((e) => ids.has(e.src) && ids.has(e.dst))
        .map<Edge>((e) => ({
          id: `${e.src}->${e.dst}`, source: e.src, target: e.dst, type: "smoothstep",
          style: { stroke: "#94a3b8", strokeWidth: 1.5 }, markerEnd: { type: MarkerType.ArrowClosed, color: "#94a3b8" },
        })),
    };
  }, [graph]);

  // Concepts fade up in a wave once React Flow has placed them.
  useEffect(() => {
    if (reducedMotion()) return;
    const id = requestAnimationFrame(() => {
      const els = box.current?.querySelectorAll(".concept-node");
      if (els?.length) animate(els, { opacity: [0, 1], y: [8, 0], delay: stagger(18), duration: 320, ease: "out(3)" });
    });
    return () => cancelAnimationFrame(id);
  }, [nodes]);

  const onNodeClick = useCallback((_: React.MouseEvent, n: Node) => onSelect(n.id), [onSelect]);

  return (
    <div ref={box} className={`${className} relative overflow-hidden rounded-xl border border-slate-200 bg-white`}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.2}
        nodesDraggable={false}
        nodesConnectable={false}
        onNodeClick={onNodeClick}
      >
        <Background gap={20} color="#e2e8f0" />
        <Controls showInteractive={false} />
      </ReactFlow>
      <div aria-hidden className="pointer-events-none absolute right-3 top-3 flex items-center gap-2 rounded-lg border border-slate-200 bg-white/90 px-2.5 py-1.5 text-[11px] text-slate-600 shadow-sm">
        Mastery
        <span className="h-1.5 w-20 rounded-full" style={{ background: `linear-gradient(90deg, ${masteryColor(0).replace("85%", "55%")}, ${masteryColor(0.5).replace("85%", "55%")}, ${masteryColor(1).replace("85%", "55%")})` }} />
        <span>0 → 100%</span>
      </div>
    </div>
  );
}
