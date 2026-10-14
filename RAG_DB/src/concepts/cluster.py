"""Merge concept mentions with similar names into concepts (TRD §7.2)."""
import hashlib
import re
from collections import Counter

import numpy as np

DISTANCE_THRESHOLD = 0.25


def deterministic_concept_id(name: str, prefix: str = "c_") -> str:
    """Derive deterministic ID from normalized name (TRD §5.2 / Issue #20).
    Trimmed to 12 hex characters for stable collision-free concept tracking.
    """
    norm = re.sub(r"\s+", " ", name.strip().lower())
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}{digest}"


def slug(name: str, prefix: str = "c_") -> str:
    """Backwards-compatible slug function returning deterministic ID."""
    return deterministic_concept_id(name, prefix=prefix)



def keep_old_ids(concepts: list[dict], old: list[tuple[str, str, list[str]]]) -> int:
    """Rebuilds name concepts afresh, so a different top mention name would mint a new id and orphan the mastery and
    quiz rows that point at the old one. Give a concept the old id of a concept whose name or alias it shares.
    `old` is [(concept_id, name, aliases)]; returns how many ids were reused through a name match."""
    norm = lambda s: re.sub(r"\s+", " ", s.strip().lower())  # noqa: E731
    old_ids = {cid for cid, _, _ in old}
    by_name = {}
    for cid, name, aliases in old:
        for n in [name, *aliases]:
            by_name.setdefault(norm(n), cid)
    claimed = {c["id"] for c in concepts if c["id"] in old_ids}  # same name as before: already the old id
    moved = 0
    for c in concepts:
        if c["id"] in old_ids:
            continue
        for n in [c["name"], *c["aliases"]]:
            cid = by_name.get(norm(n))
            if cid and cid not in claimed:
                c["id"] = cid
                claimed.add(cid)
                moved += 1
                break
    return moved


def unit_vectors(vectors) -> np.ndarray:
    v = np.array(vectors, dtype=float)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


def cluster_mentions(mentions: list[dict], encode) -> tuple[list[dict], list[int]]:
    """Cluster mentions on BGE-M3 embeddings of `name + definition`.

    Returns (concepts, owner) where owner[i] is the index in `concepts` of mention i. A concept is
    {id, name, aliases, definition, level_hint, emb, first_pos}; its name is the most frequent mention name.
    """
    vecs = unit_vectors(encode([f"{m['name']}. {m['definition']}" for m in mentions]))
    if len(mentions) > 1:
        from sklearn.cluster import AgglomerativeClustering

        labels = AgglomerativeClustering(
            n_clusters=None, metric="cosine", linkage="average", distance_threshold=DISTANCE_THRESHOLD,
        ).fit_predict(vecs)
    else:
        labels = np.zeros(len(mentions), dtype=int)

    concepts, owner, taken = [], [0] * len(mentions), set()
    for label in sorted(set(labels)):
        members = [i for i, lab in enumerate(labels) if lab == label]
        names = [mentions[i]["name"] for i in members]
        counts = Counter(n.lower() for n in names)
        top = max(counts, key=lambda n: (counts[n], -len(n)))
        name = next(n for n in names if n.lower() == top)
        seen, aliases = {top}, []
        for n in names:
            if n.lower() not in seen:
                seen.add(n.lower())
                aliases.append(n)
        base = deterministic_concept_id(name, prefix="c_")
        cid, k = base, 2
        while cid in taken:
            cid, k = f"{base}_{k}", k + 1
        taken.add(cid)
        centroid = vecs[members].mean(axis=0)
        concepts.append({
            "id": cid, "name": name, "aliases": aliases,
            "definition": next((mentions[i]["definition"] for i in members if mentions[i]["name"] == name and mentions[i]["definition"]), ""),
            "level_hint": Counter(mentions[i]["level_hint"] for i in members).most_common(1)[0][0],
            "emb": centroid / max(np.linalg.norm(centroid), 1e-12),
            "first_pos": min(mentions[i]["pos"] for i in members),
        })
        for i in members:
            owner[i] = len(concepts) - 1
    return concepts, owner
