"""Citations. Locators always come from stored fields, never from model output.

`citation_from_payload` and `resolve` build `Citation`s (with page-image and seek URLs) from
Qdrant payloads and the `units` table. `validate` checks the `[E1]` markers in a generated
answer against the evidence ids (TRD §7.4); the model never writes a page or a timestamp.
"""
import json
import re
from pathlib import Path

import config
import worker_client
from core.db import get_connection
from core.models import Citation, Locator

EXCERPT_CHARS = 300
MARKER = re.compile(r"\[(E\d+)\]")
MIN_FACTUAL_WORDS = 7  # TRD §7.4: a sentence longer than 6 words is factual unless it is a question


def body(text: str, heading: str | None) -> str:
    """Chunk text without the "subject > heading" line that embed.py prepends for contextual retrieval."""
    first, _, rest = text.partition("\n")
    return rest if rest and heading and first.endswith(heading) else text


def _page_image_url(source_id: str, loc: Locator) -> str | None:
    n = loc.page_no or loc.slide_no or loc.matched_slide_page
    # With a GPU worker the page images live there (Vercel routes /media to it), so this host cannot check for them.
    if n and (worker_client.WORKER_URL or (config.PAGES_DIR / source_id / f"{n}.webp").is_file()):
        return f"/media/pages/{source_id}/{n}.webp"
    return None


def citation_from_payload(eid: str, p: dict) -> Citation:
    """Map a Qdrant payload (or a `units` row shaped like one) to a Citation."""
    bbox = p.get("bbox")
    if isinstance(bbox, str):
        bbox = json.loads(bbox)
    source_id = p.get("source_id") or p.get("doc") or ""
    loc = Locator(
        page_no=p.get("page_no"),
        slide_no=p.get("slide_no"),
        t_start=p.get("t_start"),
        t_end=p.get("t_end"),
        bbox=bbox,
        matched_slide_page=p.get("matched_slide_page"),
    )
    media = p.get("media_path")
    return Citation(
        id=eid,
        unit_id=str(p.get("unit_id") or p.get("chunk_id") or p.get("id") or ""),
        source_id=source_id,
        source_type=p.get("source_type") or ("video" if p.get("t_start") is not None else "textbook"),
        title=p.get("title") or p.get("doc") or "",
        locator=loc,
        seek_url=_seek_url(media, loc.t_start),
        page_image_url=_page_image_url(source_id, loc),
        excerpt=body(p.get("text") or "", p.get("heading"))[:EXCERPT_CHARS],
    )


def _seek_url(media: str | None, t_start: float | None) -> str | None:
    if not media or t_start is None:
        return None
    if media.startswith(("http://", "https://")):  # URL source: no local file, link to the original at the same second
        return f"{media}{'&' if '?' in media else '?'}t={int(t_start)}s"
    parts = Path(media).parts  # uploads are stored as raw/<subject>/<file>; /media/video serves raw/
    rel = Path(*parts[parts.index("raw") + 1:]) if "raw" in parts else Path(media).name
    return f"/media/video/{rel}#t={t_start}"


def resolve(unit_ids: list[str]) -> list[Citation]:
    """Citations for unit ids from the `units` table, in input order, ids E1..En. Unknown ids are skipped."""
    if not unit_ids:
        return []
    conn = get_connection()
    conn.row_factory = lambda cur, row: {d[0]: v for d, v in zip(cur.description, row)}
    try:
        marks = ",".join("?" * len(unit_ids))
        rows = conn.execute(
            f"SELECT u.unit_id, u.source_id, u.text, u.heading, u.page_no, u.slide_no, u.t_start, u.t_end,"
            f" u.matched_slide_page, u.bbox_json AS bbox, s.title, s.kind AS source_type, s.media_path"
            f" FROM units u LEFT JOIN sources s ON s.source_id = u.source_id WHERE u.unit_id IN ({marks})",
            unit_ids,
        ).fetchall()
    finally:
        conn.close()
    by_id = {r["unit_id"]: r for r in rows}
    found = [by_id[u] for u in unit_ids if u in by_id]
    return [citation_from_payload(f"E{i}", r) for i, r in enumerate(found, 1)]


def citations_for(evidence: list) -> list[Citation]:
    """One Citation per Evidence, ids kept (E1..En), from the stored `units` row; payload fields if the row is missing."""
    by_unit = {c.unit_id: c for c in resolve([e.unit.unit_id for e in evidence])}
    out = []
    for e in evidence:
        c = by_unit.get(e.unit.unit_id) or citation_from_payload(e.id, {
            "unit_id": e.unit.unit_id, "source_id": e.unit.source_id, "source_type": e.unit.source_type,
            "title": e.unit.title, "text": e.unit.text, "heading": e.unit.heading, **e.unit.locator.model_dump(),
        })
        out.append(c.model_copy(update={"id": e.id}))
    return out


# --- marker validation (TRD §7.4) ---

def split_sentences(answer: str) -> list[str]:
    """Sentences with each marker kept inside its sentence ("... x. [E1] Next" becomes "... x [E1]. Next")."""
    answer = re.sub(r"([.!?])\s+((?:\[E\d+\]\s*)+)", lambda m: f" {m[2].strip()}{m[1]} ", answer.strip())
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if s.strip()]


def _is_factual(sentence: str) -> bool:
    text = MARKER.sub("", sentence).strip()
    return not text.endswith("?") and len(text.split()) >= MIN_FACTUAL_WORDS


def sentence_ok(sentence: str, evidence_ids: set[str]) -> bool:
    """A sentence fails if a marker is not an evidence id, or if it is factual and has no marker."""
    markers = MARKER.findall(sentence)
    return all(m in evidence_ids for m in markers) and (bool(markers) or not _is_factual(sentence))


def validate(answer: str, evidence_ids: set[str]) -> tuple[list[str], list[str]]:
    """Split an answer into (valid sentences, failed sentences)."""
    ok, bad = [], []
    for s in split_sentences(answer):
        (ok if sentence_ok(s, evidence_ids) else bad).append(s)
    return ok, bad
