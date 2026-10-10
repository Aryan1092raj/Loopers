import { masteryColor } from "@/lib/mastery";
import { topicsOf } from "@/lib/topics";
import type { Graph } from "@/lib/types";
import { Empty } from "./Note";

export default function TopicList({ graph }: { graph: Graph }) {
  const topics = topicsOf(graph);
  if (!topics.length) return <Empty>No topics yet. Upload material and wait for the concepts stage to finish.</Empty>;
  return (
    <ul className="divide-y divide-slate-100">
      {topics.map(({ node, concepts, mastery }) => {
        const pct = Math.round(mastery * 100);
        return (
          <li key={node.id} className="flex items-center gap-3 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate font-medium">{node.name}</p>
              <div className="mt-1 flex items-center gap-2 text-xs text-slate-500">
                <span className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-200" role="img" aria-label={`Mastery ${pct}%`}>
                  <span className="block h-full rounded-full" style={{ width: `${pct}%`, background: masteryColor(mastery) }} />
                </span>
                {pct}% · {concepts.length} {concepts.length === 1 ? "concept" : "concepts"}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
