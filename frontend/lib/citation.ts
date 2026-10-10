import type { Citation } from "./types";

export interface Target {
  video: { src: string; t: number } | null;
  page: { url: string; bbox: number[] | null } | null;
  /** A URL source (e.g. YouTube): there is no local file to play, so link to the original at the cited second. */
  link: string | null;
}

/** What the source panel shows for a citation. Reads stored fields only; never parses text. */
export function citationTarget(c: Citation): Target {
  const t = c.locator.t_start;
  const external = !!c.seek_url && /^https?:\/\//.test(c.seek_url);
  return {
    video: c.seek_url && !external && t != null ? { src: c.seek_url.split("#")[0], t } : null,
    page: c.page_image_url ? { url: c.page_image_url, bbox: c.locator.bbox ?? null } : null,
    link: external ? c.seek_url! : null,
  };
}

/** bbox is [x0, y0, x1, y1] in page-image pixels; percentages keep the overlay correct when the image is resized. */
export function bboxStyle(bbox: number[], naturalW: number, naturalH: number) {
  if (!naturalW || !naturalH || bbox.length !== 4) return null;
  const [x0, y0, x1, y1] = bbox;
  return {
    left: `${(x0 / naturalW) * 100}%`,
    top: `${(y0 / naturalH) * 100}%`,
    width: `${((x1 - x0) / naturalW) * 100}%`,
    height: `${((y1 - y0) / naturalH) * 100}%`,
  };
}

/** Chip text: "Lecture 04 · 23:45", "Slides · slide 18", "Chapter 5 · p. 142". */
export function chipLabel(c: Citation): string {
  const l = c.locator;
  if (l.t_start != null) {
    const s = Math.floor(l.t_start);
    return `${c.title} · ${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }
  if (l.slide_no != null) return `${c.title} · slide ${l.slide_no}`;
  if (l.page_no != null) return `${c.title} · p. ${l.page_no}`;
  return c.title;
}
