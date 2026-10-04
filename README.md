# Loopers — Multimodal AI Study Companion

Track D · Personalized Tutoring & Adaptive Learning · Multimodal AI Hackathon 2026

An AI study companion that unifies **lecture videos, textbooks, and slide decks**
into one source-cited knowledge base, then uses it for **grounded tutoring** and
**adaptive assessment** with per-topic mastery tracking.

## How it works

```
videos / textbooks / slides
        │  ingest (no manual preprocessing)
        ▼
multimodal knowledge base (topics → concepts → prerequisites,
every unit linked to page / slide / video timestamp)
        ├─► source-grounded tutor chat (cited excerpts, refuses off-material queries)
        └─► adaptive quizzes (MCQ / short / numerical, verified, no repeats)
                └─► learner model (per-topic mastery, cold-start diagnostic)
```

## Repo layout

```
RAG_DB/                  # ingestion → retrieval → generation pipeline
  src/                   # ingest, extract, parsers/, label, embed, store,
                         # generate, chat, assignment, eval, knowledge_graph, …
  templates/             # dashboard.html, eval question set example
  raw/ processed/ metadata/ qdrant_db/   # local-only (gitignored scaffolds)
whisper-server/          # local transcription service
docker-compose.rag.yml   # opt-in overlay to run the RAG service
```

> Only `README.md` is tracked on GitHub. All other markdown
> (specs, gap analysis, cleaning notes) stays local by `.gitignore` policy.

## Quickstart

```bash
cd RAG_DB
python src/pipeline.py ingest
python src/pipeline.py extract
python src/pipeline.py label     # needs GEMINI_API_KEY
python src/pipeline.py embed
python src/pipeline.py eval --k 5
python src/server.py             # dashboard at http://localhost:8000
```

## Corpus

Interim text corpus while the demo course is undecided: OpenStax *Calculus
Volume 1* (Strang, Herman et al., Rice University), licensed CC BY-NC-SA 4.0.
Fetched with `python src/pipeline.py download --source openstax --subjects calculus-vol1`
into `RAG_DB/raw/openstax_calculus-vol1.pdf` (gitignored, not redistributed here).
The planned demo course is MIT OCW 18.06 Linear Algebra (CC BY-NC-SA 4.0).

## Status

Base pipeline (text/PDF ingestion, hybrid retrieval, generation, dashboard)
is in place. In progress: video ingestion with timestamps, calibrated refusal,
exact-coordinate citations, generator–verifier assessment loop, BKT/IRT
learner model, RAGAS/DeepEval benchmark, and demo corpus + video..
