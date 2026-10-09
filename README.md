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
  templates/             # slide spec schema, eval question set example
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
cd src && LOOPERS_NO_REEXEC=1 uvicorn api.main:app --reload   # API + console at http://localhost:8000
```

## Documents, concepts and the tutor

Every chunk of a slide deck or textbook becomes a *unit* with a stored locator: a page number for PDFs and
a slide number for decks. `extract` writes a `<!-- page N -->` marker before each page, `embed` turns the
markers into `page_no` or `slide_no`, and ingestion fails if a PDF chunk ends up without one. Units live in
the SQLite `units` table and in the Qdrant payload, keyed by `source_id` (the first 16 hex characters of the
file's sha256, the same id `POST /api/sources` returns). Page images are rendered to
`processed/media/pages/<source_id>/<n>.webp` and served at `/media/pages/...`.

Figures are cropped during `extract`. Photos come from the PDF's images; graphs and diagrams are usually drawn
as vector paths, so they are found through their "Figure N.M" caption. The page and bounding box of each
crop (in page-image pixels) are written to `metadata/figures/<stem>.json`, and `label` copies them into the
figure's unit so a citation can draw the box on the page image.

`python src/pipeline.py concepts` builds the concept graph from the units and tags every unit with its
concepts. It extracts concepts with an LLM, merges near-duplicates by embedding distance, groups them under
topics, scores prerequisite edges, removes cycles and stores the result in `concepts`, `concept_edges` and
`unit_concepts`. It needs an LLM key in `.env`; without one the stage is skipped and the API serves the
fixture graph. `GET /api/graph` and `GET /api/concepts/{id}` read the stored graph.

`POST /api/chat` answers from the material only:

1. Retrieval returns evidence `E1..En`, plus background evidence for the prerequisites of the concepts in
   the question.
2. The gate refuses when the best reranker score is below `tau_low`. Between `tau_low` and `tau_high` an
   entailment check must pass. Thresholds come from `metadata/gate_thresholds.json`, else placeholders.
3. The model writes `[E1]` markers and never a page or timestamp. Every factual sentence needs a valid
   marker; failing sentences are rewritten once and dropped if they still fail, which makes the answer
   `partially_grounded`. Citations are resolved from the stored units, with page-image and video-seek URLs.
4. Sessions and turns are stored in SQLite, so two sessions never share history. With `allow_outside` a
   refused question is answered from general knowledge and marked `outside_syllabus`.

Without an LLM key the chat endpoint quotes the best evidence with its markers instead of generating text.

## Corpus

Interim text corpus while the demo course is undecided: OpenStax *Calculus
Volume 1* (Strang, Herman et al., Rice University), licensed CC BY-NC-SA 4.0.
Fetched with `python src/pipeline.py download --source openstax --subjects calculus-vol1`
into `RAG_DB/raw/openstax_calculus-vol1.pdf` (gitignored, not redistributed here).
The pipeline is subject-agnostic; any course material dropped into `raw/` works.

## Status

Base pipeline (text/PDF ingestion, hybrid retrieval, generation, dashboard)
is in place. In progress: video ingestion with timestamps, calibrated refusal,
exact-coordinate citations, generator–verifier assessment loop, BKT/IRT
learner model, RAGAS/DeepEval benchmark, and demo corpus + video..
