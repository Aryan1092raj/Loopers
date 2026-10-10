import { chipLabel } from "@/lib/citation";
import type { Citation } from "@/lib/types";

export default function CitationChip({ citation, onCite }: { citation: Citation; onCite: (c: Citation) => void }) {
  return (
    <button
      type="button"
      onClick={() => onCite(citation)}
      title={citation.excerpt}
      className="mx-0.5 inline-flex items-center rounded-full border border-blue-300 bg-blue-50 px-2 py-0.5 align-baseline text-xs text-blue-900 hover:bg-blue-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
    >
      {chipLabel(citation)}
    </button>
  );
}
