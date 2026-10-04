"""Tag every unit with concepts (TRD §7.2)."""
import re

import numpy as np

from concepts.cluster import unit_vectors

THRESHOLD = 0.45  # cosine between unit and concept embeddings
TOP = 3
NEAREST = 10      # candidates offered to the LLM when no concept reaches THRESHOLD

PROMPT = (
    "Which of these concepts does the passage teach? Candidates:\n{names}\n\nPassage:\n{text}\n\n"
    "Reply with the names of 1 to 3 candidates, one per line, or NONE."
)


def tag_units(units: list[dict], unit_vecs, concepts: list[dict], llm=None) -> tuple[dict[str, list[tuple[str, float]]], list[str]]:
    """Tags {unit_id: [(concept_id, score)]} for units [{unit_id, text}] and the ids of weakly tagged units.

    Top 3 concepts with cosine >= 0.45. If none qualifies the LLM picks among the 10 nearest; if it
    picks none the nearest concept is kept and the unit is reported as weak instead of failing the ingest.
    """
    sims = unit_vectors(unit_vecs) @ np.array([c["emb"] for c in concepts]).T
    tags, weak = {}, []
    for unit, row in zip(units, sims):
        order = [int(i) for i in np.argsort(-row)]
        picked = [(concepts[i]["id"], float(row[i])) for i in order[:TOP] if row[i] >= THRESHOLD]
        if not picked and llm is not None:
            near = order[:NEAREST]
            reply = llm(PROMPT.format(names="\n".join(concepts[i]["name"] for i in near), text=unit["text"][:1500]))
            lines = {re.sub(r"^[-*\d.)\s]+", "", ln).strip().lower() for ln in reply.splitlines()}
            picked = [(concepts[i]["id"], float(row[i])) for i in near if concepts[i]["name"].lower() in lines][:TOP]
        if not picked:
            picked = [(concepts[order[0]]["id"], float(row[order[0]]))]
            weak.append(unit["unit_id"])
        tags[unit["unit_id"]] = picked
    return tags, weak
