"""Topic / subtopic / concept levels (TRD §7.2).

One LLM call proposes the course topics from the concept names; every other concept attaches to the
topic whose embedding is nearest.
"""
import json
import re

import numpy as np

from concepts.cluster import slug, unit_vectors

MAX_NAMES = 300  # concept names shown to the LLM, sampled evenly in teaching order

PROMPT = (
    "These concepts come from one course, listed in the order they are taught:\n{names}\n\n"
    "Propose between 5 and 12 course topics that group them. Return only a JSON array of short topic names "
    "(2 to 4 words each)."
)


def propose_topics(concepts: list[dict], llm, course: str) -> list[str]:
    ordered = sorted(concepts, key=lambda c: c["first_pos"])
    sample = ordered[:: max(1, -(-len(ordered) // MAX_NAMES))]
    found = re.search(r"\[.*\]", llm(PROMPT.format(names="\n".join(c["name"] for c in sample))), re.S)
    try:
        names = [str(n).strip() for n in json.loads(found.group(0))] if found else []
    except json.JSONDecodeError:
        names = []
    return list(dict.fromkeys(n for n in names if n)) or [course]


def build_hierarchy(concepts: list[dict], encode, llm, course: str = "Course") -> list[dict]:
    """Add `level` and `parent_id` to every concept and return the topic nodes (level "topic")."""
    names = propose_topics(concepts, llm, course)
    vecs = unit_vectors(encode(names))
    taken = {c["id"] for c in concepts}
    topics = []
    for name, emb in zip(names, vecs):
        tid = slug(name)
        while tid in taken:
            tid += "_topic"
        taken.add(tid)
        topics.append({"id": tid, "name": name, "aliases": [], "definition": "", "level": "topic", "parent_id": None, "emb": emb})
    topic_matrix = np.array([t["emb"] for t in topics])
    for c in concepts:
        c["level"] = "subtopic" if c["level_hint"] == "subtopic" else "concept"
        c["parent_id"] = topics[int(np.argmax(topic_matrix @ c["emb"]))]["id"]
    return topics
