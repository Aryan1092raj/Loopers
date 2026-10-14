import { chipLabel, citationTarget } from "@/lib/citation";
import type { Citation } from "@/lib/types";
import PageCanvas from "./PageCanvas";
import VideoPlayer from "./VideoPlayer";

export default function SourcePanel({ active }: { active: { c: Citation; n: number } | null }) {
  if (!active) {
    return <section aria-label="Source" className="rounded border border-dashed bg-white p-6 text-sm text-neutral-500">Click a citation chip to open its source here.</section>;
  }
  const { c, n } = active;
  const target = citationTarget(c);
  return (
    <section aria-label="Source" className="space-y-3 rounded border bg-white p-4">
      <h2 className="text-xl font-semibold">{chipLabel(c)}</h2>
      {target.video && <VideoPlayer key={target.video.src} src={target.video.src} t={target.video.t} n={n} />}
      {target.link && (
        <a href={target.link} target="_blank" rel="noopener noreferrer" className="inline-block rounded border px-3 py-1 text-sm underline hover:bg-neutral-100">
          Open the video at this moment
        </a>
      )}
      {target.page && <PageCanvas key={target.page.url} url={target.page.url} bbox={target.page.bbox} alt={`${c.title} page`} />}
      {!target.video && !target.page && !target.link && <p className="text-xs text-neutral-500">No video or page image is stored for this source.</p>}
      <blockquote className="border-l-4 border-neutral-300 pl-3 text-sm text-neutral-700">{c.excerpt}</blockquote>
    </section>
  );
}
