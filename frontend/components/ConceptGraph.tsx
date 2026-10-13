"use client";
import "@xyflow/react/dist/style.css";
import { animate, stagger, svg } from "animejs";
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps, type ReactFlowInstance } from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { layerOf, layoutGraph, revealOrder } from "@/lib/layout";
import { masteryColor } from "@/lib/mastery";
import { reducedMotion } from "@/lib/motion";
import { topicsOf } from "@/lib/topics";
import type { Graph } from "@/lib/types";

type ConceptData = { name: string; p: number; layer: number };

// One muted hue per prerequisite layer (0 = no prerequisites); layer 6 onwards reuses them.
const LAYER_COLORS = ["#6b8cae", "#7fa88a", "#c9a24a", "#c47b64", "#9a7fb0", "#5fa3a3"];
const layerColor = (layer: number) => LAYER_COLORS[layer % LAYER_COLORS.length];

/** One concept: name plus a mastery bar, so the number does not rely on colour alone. The left edge shows its layer. */
function ConceptNode({ data, selected }: NodeProps<Node<ConceptData>>) {
  const pct = Math.round(data.p * 100);
  return (
    <div
      className={`concept-node min-w-[240px] cursor-pointer rounded-xl border border-l-4 bg-white px-3.5 py-2.5 text-left shadow-sm transition-shadow hover:shadow-md ${selected ? "border-indigo-500 ring-2 ring-indigo-200" : "border-slate-200"}`}
      style={selected ? undefined : { borderLeftColor: layerColor(data.layer) }}
      title={`${data.name}: layer ${data.layer + 1}, mastery ${pct}%`}
    >
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-0 !bg-slate-300" />
      <p className="truncate text-xs md:text-sm font-semibold text-slate-900 leading-snug">{data.name}</p>
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

/** Fade an edge in and send a dot along it, from its prerequisite to the concept that needs it. */
function travel(edge: Element, color: string, dots: boolean) {
  const path = edge.querySelector<SVGPathElement>("path.react-flow__edge-path");
  if (!path) return;
  animate(path, { opacity: [0, 1], duration: 500 });
  if (!dots) return;
  // The dot is a temporary child of React Flow's own edge <g>, so it shares the edge's coordinates.
  const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  dot.setAttribute("r", "5");
  dot.setAttribute("fill", color);
  dot.style.pointerEvents = "none";
  edge.appendChild(dot);
  const { translateX, translateY } = svg.createMotionPath(path);
  animate(dot, { translateX, translateY, duration: 800, ease: "inOutSine", onComplete: () => dot.remove() });
}

const DOT_LIMIT = 12; // "Show all" reveals hundreds of edges at once: fade them in, skip the dots

export default function ConceptGraph({ graph, onSelect, onReload, className = "h-[70vh]" }: { graph: Graph; onSelect: (id: string) => void; onReload?: () => void; className?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const flow = useRef<ReactFlowInstance<Node<ConceptData>> | null>(null);
  const [topicId, setTopicId] = useState("");

  const topics = useMemo(() => topicsOf(graph), [graph]);
  // Everything that depends on which concepts are in scope; a new object means "start over from one node".
  const scope = useMemo(() => {
    const topic = topics.find((t) => t.node.id === topicId);
    const concepts = graph.nodes.filter((n) => n.level !== "topic");
    const pool = topic ? topic.concepts : concepts.length ? concepts : graph.nodes;
    const ids = new Set(pool.map((n) => n.id));
    const edges = graph.edges.filter((e) => ids.has(e.src) && ids.has(e.dst));
    return {
      order: revealOrder(pool, edges),
      edges,
      layer: layerOf(pool, edges),
      pos: new Map(layoutGraph(pool, edges).map((p) => [p.id, p])),
    };
  }, [graph, topics, topicId]);

  const total = scope.order.length;
  const [reveal, setReveal] = useState({ scope, n: 1 });
  const n = Math.min(reveal.scope === scope ? reveal.n : 1, total);
  const next = useCallback(() => setReveal({ scope, n: Math.min(n + 1, total) }), [scope, n, total]);
  const showAll = () => setReveal({ scope, n: total });
  const reload = () => { setReveal({ scope, n: 1 }); onReload?.(); };

  const { nodes, edges, layers } = useMemo(() => {
    const visible = scope.order.slice(0, n);
    const ids = new Set(visible.map((v) => v.id));
    return {
      nodes: visible.map<Node<ConceptData>>((v) => ({
        id: v.id,
        type: "concept",
        position: { x: scope.pos.get(v.id)?.x ?? 0, y: scope.pos.get(v.id)?.y ?? 0 },
        data: { name: v.name, p: v.p_know, layer: scope.layer.get(v.id) ?? 0 },
      })),
      edges: scope.edges
        .filter((e) => ids.has(e.src) && ids.has(e.dst))
        .map<Edge>((e) => ({
          id: `${e.src}->${e.dst}`, source: e.src, target: e.dst, type: "smoothstep",
          style: { stroke: "#94a3b8", strokeWidth: 1.5 }, markerEnd: { type: MarkerType.ArrowClosed, color: "#94a3b8" },
        })),
      layers: [...new Set(visible.map((v) => scope.layer.get(v.id) ?? 0))].sort((a, b) => a - b),
    };
  }, [scope, n]);

  // Each reveal: frame the map, pop the new concepts in, and run a dot along every edge that just connected.
  const shown = useRef({ scope, n: 0 });
  useEffect(() => {
    const t = setTimeout(() => {
      const before = shown.current.scope === scope ? shown.current.n : 0;
      shown.current = { scope, n };
      const fresh = new Set(scope.order.slice(before, n).map((v) => v.id));
      const still = reducedMotion();
      flow.current?.fitView({ padding: 0.25, maxZoom: 1, duration: still ? 0 : 400 });
      const root = box.current;
      if (!fresh.size || !root || still) return; // stepping back only re-frames the map
      const els = Array.from(root.querySelectorAll<HTMLElement>(".react-flow__node")).filter((el) => fresh.has(el.dataset.id ?? ""));
      animate(els.map((el) => el.querySelector(".concept-node")).filter(Boolean) as Element[], { opacity: [0, 1], y: [8, 0], delay: stagger(18), duration: 320, ease: "out(3)" });
      const visible = new Set(scope.order.slice(0, n).map((v) => v.id));
      const connecting = scope.edges.filter((e) => visible.has(e.src) && visible.has(e.dst) && (fresh.has(e.src) || fresh.has(e.dst)));
      const byId = new Map(Array.from(root.querySelectorAll(".react-flow__edge")).map((el) => [el.getAttribute("data-id"), el]));
      for (const e of connecting) {
        const el = byId.get(`${e.src}->${e.dst}`);
        if (el) travel(el, layerColor(scope.layer.get(e.dst) ?? 0), connecting.length <= DOT_LIMIT);
      }
    }, 60); // React Flow measures new nodes on the next frame
    return () => clearTimeout(t);
  }, [scope, n]);

  // One-finger (or left-button) drag on empty space: a horizontal swipe in either direction = next concept. Two-finger swipes pan the map instead.
  const swipe = useRef<{ x: number; y: number } | null>(null);
  const onPointerDown = (e: React.PointerEvent) => {
    const onPane = (e.target as HTMLElement).classList.contains("react-flow__pane");
    swipe.current = onPane && e.isPrimary && e.button === 0 ? { x: e.clientX, y: e.clientY } : null;
  };
  const onPointerUp = (e: React.PointerEvent) => {
    const s = swipe.current;
    swipe.current = null;
    if (!s) return;
    const dx = e.clientX - s.x, dy = e.clientY - s.y;
    if (Math.abs(dx) >= 80 && Math.abs(dy) < Math.abs(dx) / 2) next();
  };

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => onSelect(node.id), [onSelect]);

  return (
    <div ref={box} onPointerDown={onPointerDown} onPointerUp={onPointerUp} onPointerCancel={() => { swipe.current = null; }} className={`${className} relative overflow-hidden rounded-xl border border-slate-200 bg-white`}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onInit={(i) => { flow.current = i; }}
        minZoom={0.2}
        nodesDraggable={false}
        nodesConnectable={false}
        panOnDrag={[1, 2]}
        panOnScroll
        zoomOnScroll={false}
        onNodeClick={onNodeClick}
      >
        <Background gap={20} color="#e2e8f0" />
        <Controls showInteractive={false} />
      </ReactFlow>
      <div className="absolute left-3 top-3 z-10 flex flex-wrap items-center gap-2 rounded-lg border border-slate-200 bg-white/90 px-2.5 py-1.5 text-xs text-slate-700 shadow-sm">
        <label className="sr-only" htmlFor="map-topic">Topic</label>
        <select id="map-topic" value={topicId} onChange={(e) => setTopicId(e.target.value)} className="max-w-48 rounded-lg border border-slate-200 bg-white px-2 py-1">
          <option value="">All topics</option>
          {topics.map((t) => <option key={t.node.id} value={t.node.id}>{t.node.name}</option>)}
        </select>
        <span aria-live="polite" className="tabular-nums">{n} of {total}</span>
        <button type="button" onClick={showAll} disabled={n >= total} className="rounded-lg border border-slate-200 bg-white px-2.5 py-1 transition hover:border-indigo-300 disabled:opacity-40">Show all</button>
      </div>
      <div className="absolute bottom-3 right-3 z-10 flex flex-col items-end gap-2">
        <button type="button" onClick={next} disabled={n >= total} className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm text-white shadow-sm transition hover:bg-indigo-500 disabled:opacity-40">Next</button>
        <button type="button" onClick={reload} aria-label="Reload concepts and rebuild the graph" title="Reload concepts and rebuild the graph" className="rounded-lg border border-slate-200 bg-white p-2 text-slate-600 shadow-sm transition hover:border-indigo-300 disabled:opacity-40">
          <svg aria-hidden viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7" /><path d="M21 3v6h-6" /></svg>
        </button>
      </div>
      <div className="pointer-events-none absolute bottom-3 left-16 z-10 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-slate-200 bg-white/90 px-2.5 py-1.5 text-[11px] text-slate-600 shadow-sm">
        {layers.map((l) => (
          <span key={l} className="flex items-center gap-1">
            <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: layerColor(l) }} />
            {l === 0 ? "Layer 1 (no prerequisites)" : `Layer ${l + 1}`}
          </span>
        ))}
      </div>
      <div aria-hidden className="pointer-events-none absolute right-3 top-3 flex items-center gap-2 rounded-lg border border-slate-200 bg-white/90 px-2.5 py-1.5 text-[11px] text-slate-600 shadow-sm">
        Mastery
        <span className="h-1.5 w-20 rounded-full" style={{ background: `linear-gradient(90deg, ${masteryColor(0).replace("85%", "55%")}, ${masteryColor(0.5).replace("85%", "55%")}, ${masteryColor(1).replace("85%", "55%")})` }} />
        <span>0 → 100%</span>
      </div>
    </div>
  );
}
