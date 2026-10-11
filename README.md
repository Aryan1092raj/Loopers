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
cd src && LOOPERS_NO_REEXEC=1 uvicorn api.main:app   # API only (no --reload: a reload orphans a running ingest job) at http://localhost:8000 (UI: frontend on :3000)
```

## Run with Docker

Full stack on CPU (qdrant, api, web, optional whisper). Copy the env file first and add the LLM keys:

```bash
cp RAG_DB/.env.example RAG_DB/.env
docker compose -f docker-compose.app.yml up -d --build              # UI :3000, API :8000
docker compose -f docker-compose.app.yml --profile asr up -d        # adds the whisper server
docker compose -f docker-compose.app.yml logs -f api                # tail the api
docker compose -f docker-compose.app.yml run --rm api python src/pipeline.py embed
docker compose -f docker-compose.app.yml down                       # stop; volumes keep the data
```

Docker Desktop runs containers in a VM with no GPU. Give that VM at least 8 GB of memory (Settings →
Resources); at about 3.5 GB the `embed` stage is killed with `exited with code -9` and the UI shows
"Failed to fetch" while the api restarts. On the native Docker Engine with the NVIDIA Container Toolkit
(`docker context use default`), add the GPU overlay so the api runs on the GPU:

```bash
docker compose -f docker-compose.app.yml -f docker-compose.gpu.yml --profile asr up -d --build
```

On a 6 GB card the overlay leaves Whisper on CPU (int8): the api's models and an ingest job already use most
of the card. On a card with 8 GB or more set `WHISPER_DEVICE=cuda WHISPER_COMPUTE_TYPE=float16` before `up`.
The reranker visits the GPU only while it scores a query, then returns to CPU memory
(`RERANK_KEEP_ON_GPU=1` keeps it resident). If port 8000 or 3000 is taken, set `API_PORT` and `WEB_PORT`.

Without Docker GPU support, run only qdrant in Docker and the rest on the host (the host GPU is used
through the `.venv` CUDA build of torch):

```bash
docker compose -f docker-compose.app.yml up -d qdrant
cd RAG_DB/src && QDRANT_URL=http://localhost:6333 LOOPERS_NO_REEXEC=1 uvicorn api.main:app --port 8000
python whisper-server/server.py --port 8300      # large-v3-turbo, float16 on the GPU
cd frontend && npm run dev                       # UI :3000, proxies /api to localhost:8000
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

The answer adapts to the student. Before generating, the tutor reads the student's BKT mastery of the concepts
tagged on the answering units: below 0.40 it explains step by step, defines terms first, leans on the
prerequisite evidence and ends with a check question; at 0.75 or above it answers concisely and points out the
subtle detail; in between it gives one worked example. Every grounded turn then nudges that mastery up.

Without an LLM key the chat endpoint quotes the best evidence with its markers instead of generating text.

## Assessment and the learner model

`POST /api/quiz/generate` writes one item (multiple choice, short answer or numeric) from the source units
tagged to a concept or topic, and records which units it came from, so feedback cites stored locators. Three
checks run before the item is served: the stem must not be a near-duplicate of an earlier item for that
concept, a different model solves it blind and must reach the same key (`solver_conflict` otherwise), and
the generator is told which questions were already asked. The answer key never leaves the server until the
student submits. Grading is by type (exact option, numeric tolerance, fuzzy match for short answers), and the
post-quiz report lists weak concepts, recorded misconceptions and the mastery change per concept.

`POST /api/quizzes` builds a practice quiz or a timed mock exam (`mode: "mock"`, 90 s per item) over the
topics and concepts the student picks, with a mix such as `{"mcq": 4, "short": 2, "numeric": 2}` and an
optional target difficulty (IRT b). Slots rotate over the in-scope concepts, weakest mastery first. Each slot is
filled from the verified bank if the student has never been served that item, otherwise by generating a new
item through the same three checks, so questions do not repeat across assessments. Every served item carries
its topic, concept, Bloom level, difficulty label and source locations (page, slide or timestamp).
`POST /api/quizzes/{id}/answers` grades a batch and returns per-item correctness, the cited explanation, the
misconception behind a wrong option and the mastery updates; `GET /api/quizzes/{id}/report` gives the
post-assessment report.

Mastery is a Bayesian knowledge tracing estimate per concept (slip 0.10, guess 0.20, learn 0.05 per answered
item, 0.02 per grounded chat turn). Each answer also updates a simple FSRS-style stability, which sets the next
review date. A new student answers an intake question (background sets the prior ability and starting
mastery) and a diagnostic of up to five verified multiple-choice items, scored with a one-parameter IRT
model. `GET /api/students/{id}/schedule` plans study days up to the exam date: weak or overdue concepts come
first, a concept unlocks once its prerequisites reach 0.70, and each day is capped at `minutes_per_day`.

## Evaluation

All of it lives in `RAG_DB/src/evaluation/` and is saved to the `eval_runs` table, which the web app shows at
`/eval`.

- `python -m evaluation.run --k 5 --indexed-only` measures hit@k, MRR and locator precision against the golden
  set, per question type, and calibrates the refusal gate (`--write-thresholds` writes
  `metadata/gate_thresholds.json`). It stops if the cross-encoder fails instead of calibrating on the wrong
  score scale.
- `--answers --limit N` also answers questions through the tutor and reports citation accuracy, refusal
  honesty, over-refusal and four judge metrics (faithfulness, answer relevancy, context precision, context
  recall). The judge is a different model from the generator. `--dump file.json` saves the samples, and
  `python -m evaluation.deepeval_check file.json` scores the same samples with DeepEval in a separate
  virtualenv as a cross-check (its dependencies conflict with the main environment).
- `python -m evaluation.gen_golden` writes golden questions for whatever is indexed, copying gold locators
  from stored fields; `python -m evaluation.golden --spotcheck` prints 20 random items to open by hand.
- `python -m evaluation.simulate` runs simulated students (fast, average and slow learners) through ten
  sessions, adaptive versus a random-question control, and reports mastery gain and question repetition.

The golden set and simulated students are generated, so treat the numbers as a check on the pipeline, not as
a benchmark. Results are in the Status section.

## Testing

`docs/TESTING.md` walks through every service by hand with copy-paste commands, and
`scripts/e2e_smoke.sh` runs the main path end to end against a running API (`SMOKE_VIDEO` adds a lecture
video). Backend unit tests: `cd RAG_DB && python -m pytest tests -q`.

## Corpus

The demo course is *Limits and Continuity*, in all three modalities, uploaded through `POST /api/sources`
with no manual preprocessing:

- Textbook: OpenStax *Calculus Volume 1*, Chapter 2 through section 2.4 (book pages 113–176, 64 pages),
  CC BY-NC-SA 4.0. Cut from the full PDF fetched with `python src/pipeline.py download --source openstax
  --subjects calculus-vol1`.
- Lecture video: MIT OpenCourseWare 18.01 Single Variable Calculus, Fall 2007, Lecture 2 "Limits,
  Continuity; Trigonometric Limits" (52:46), CC BY-NC-SA 4.0, https://www.youtube.com/watch?v=ryLdyDrBfvI.
- Slides: a 12-slide deck on limits and continuity written for this project, with four plotted figures.

None of these files are redistributed here. The pipeline is subject-agnostic; any course material works.

## Status

Base pipeline (text/PDF ingestion, hybrid retrieval, generation, dashboard)
is in place. In progress: video ingestion with timestamps, calibrated refusal,
exact-coordinate citations, generator–verifier assessment loop, BKT/IRT
learner model, RAGAS/DeepEval benchmark, and demo corpus + video..
