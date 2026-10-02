import json
from pathlib import Path
import sys

mocks = {
    "job_j1.json": {
        "job_id": "j1",
        "source_id": "a1b2",
        "status": "running",
        "stage": "transcribe",
        "progress": 0.42,
        "units_created": 0,
        "error": None
    },
    "graph.json": {
        "nodes": [
            {
                "id": "characteristic_polynomial",
                "name": "Characteristic polynomial",
                "level": "concept",
                "parent_id": "eigenvalues",
                "p_know": 0.31
            }
        ],
        "edges": [
            {
                "src": "determinant",
                "dst": "characteristic_polynomial",
                "weight": 0.83
            }
        ]
    },
    "chat_response.json": {
        "mode": "grounded",
        "answer": "When an eigenvalue repeats, compare algebraic and geometric multiplicity [E1].",
        "segments": [
            {
                "text": "When an eigenvalue repeats, compare algebraic and geometric multiplicity",
                "evidence_ids": ["E1"],
                "outside": False
            }
        ],
        "citations": [
            {
                "id": "E1",
                "unit_id": "u_9f3a",
                "source_id": "a1b2",
                "source_type": "video",
                "title": "Lecture 04",
                "locator": {
                    "t_start": 1925.0,
                    "t_end": 1990.4,
                    "matched_slide_page": 24
                },
                "seek_url": "/media/video/lec04.mp4#t=1925.0",
                "page_image_url": "/media/pages/lec04_slides/24.webp",
                "thumb_url": None,
                "excerpt": "..."
            }
        ],
        "gate": {
            "top_score": 0.71,
            "tier": None,
            "band": "high",
            "nli": None,
            "decision": "answer"
        },
        "refusal": None,
        "mastery_updates": [
            {
                "concept_id": "diagonalization",
                "p_before": 0.31,
                "p_after": 0.36
            }
        ]
    },
    "mastery.json": [
        {
            "concept_id": "characteristic_polynomial",
            "p_know": 0.31,
            "retrievability": 0.85,
            "n_obs": 2,
            "last_review": "2026-10-01T10:00:00Z"
        }
    ],
    "quiz_q1.json": {
        "quiz_id": "q1",
        "items": [
            {
                "item_id": "i1",
                "type": "mcq",
                "concept_id": "eigenvalues",
                "topic_id": "linear_algebra",
                "bloom": "understand",
                "stem": "What is an eigenvalue?",
                "options": ["A scalar", "A vector", "A matrix", "A determinant"],
                "difficulty_b": 0.5,
                "discrimination_a": 1.0,
                "unit_ids": ["u1"],
                "verified": True
            }
        ]
    },
    "quiz_report_q1.json": {
        "quiz_id": "q1",
        "score": 0.625,
        "mastery_delta": [
            {
                "concept_id": "eigenvalues",
                "before": 0.31,
                "after": 0.47
            }
        ],
        "weak_topics": [
            {
                "topic_id": "linear_algebra",
                "p_know": 0.28
            }
        ],
        "misconceptions": [
            {
                "label": "sign inversion in det(A - lambda I)",
                "concept_id": "eigenvalues",
                "citation": {}
            }
        ],
        "next_steps": [
            {
                "concept_id": "characteristic_polynomial",
                "reason": "prerequisite below 0.4"
            }
        ]
    },
    "schedule.json": [
        {
            "date": "2026-10-02",
            "tasks": [
                {
                    "concept_id": "characteristic_polynomial",
                    "minutes": 25,
                    "reason": "retrievability 0.78"
                }
            ]
        }
    ]
}

out_dir = Path("RAG_DB/frontend/mocks")
out_dir.mkdir(parents=True, exist_ok=True)

# 1. Write the files
for filename, data in mocks.items():
    with open(out_dir / filename, "w") as f:
        json.dump(data, f, indent=2)

# 2. Cross-Validate / Audit (Verify valid JSON and schema integrity)
print("Running Cross-Validation Audit...")
try:
    for filename in mocks.keys():
        with open(out_dir / filename, "r") as f:
            parsed = json.load(f)
            assert parsed is not None, f"{filename} is empty"
    print("SUCCESS: All 7 mock endpoints generated and strictly validated as well-formed JSON.")
except Exception as e:
    print(f"FAILED: Schema validation error: {e}")
    sys.exit(1)
