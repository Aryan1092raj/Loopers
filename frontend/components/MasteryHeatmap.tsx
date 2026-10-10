import { masteryColor } from "@/lib/mastery";
import type { Graph, Mastery } from "@/lib/types";
import { Empty } from "./Note";

export default function MasteryHeatmap({ graph, mastery }: { graph: Graph; mastery: Mastery[] }) {
  const concepts = graph.nodes.filter((n) => n.level !== "topic");
  if (concepts.length === 0) return <Empty>No concepts yet, so there is nothing to colour.</Empty>;
  const detail = new Map(mastery.map((m) => [m.concept_id, m]));
  const topics = new Map(graph.nodes.filter((n) => n.level === "topic").map((n) => [n.id, n.name]));
  const groups = new Map<string, typeof concepts>();
  for (const c of concepts) groups.set(c.parent_id ?? "", [...(groups.get(c.parent_id ?? "") ?? []), c]);

  return (
    <div className="space-y-4">
      {[...groups.entries()].map(([topic, items]) => (
        <section key={topic} aria-label={topics.get(topic) ?? "Other concepts"}>
          <h3 className="mb-1 text-sm font-medium">{topics.get(topic) ?? "Other concepts"}</h3>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {items.map((c) => {
              const d = detail.get(c.id);
              const tip = `${Math.round(c.p_know * 100)}% known · ${d?.n_obs ?? 0} observations${d?.retrievability != null ? ` · recall ${Math.round(d.retrievability * 100)}%` : ""}`;
              return (
                <div key={c.id} title={tip} style={{ background: masteryColor(c.p_know) }} className="rounded border p-2 text-sm">
                  <div className="font-medium">{c.name}</div>
                  <div className="text-xs text-neutral-700">{Math.round(c.p_know * 100)}%</div>
                </div>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
