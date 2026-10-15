# Loopers — Multimodal AI Study Companion

Track D · Personalized Tutoring & Adaptive Learning · Multimodal AI Hackathon 2026

Loopers turns **lecture videos, textbooks and slide decks** into one source-cited knowledge base. A tutor answers
only from that material and cites the exact page, slide or video second. Quizzes are verified before they are
served, and a learner model tracks mastery per concept. The language model never writes a page number or
timestamp: it writes markers like `[E1]`, and the server resolves them from stored fields.

## Models

| Job | Model |
|---|---|
| Embeddings (text, figures, video clips) | `google/embeddinggemma-2` (gated: accept the license on Hugging Face) |
| Reranker and refusal gate | `BAAI/bge-reranker-v2-m3` cross-encoder |
| Page-image retrieval (optional, off by default) | `vidore/colSmol-500M` |
| Speech to text | faster-whisper `large-v3-turbo` on GPU, `base` on CPU |
| Text to speech | Piper `en_US-lessac-medium` |
| Tutor answers, concept graph, quiz generation | DeepSeek `deepseek-flash`, falling back to Groq `gpt-oss-120b`, then Gemini `3.5-flash-lite` |
| Blind quiz solver, answer checks | Groq `gpt-oss-120b`, then Gemini, then DeepSeek (never the generator first) |
| Figure captions | Gemini |
| Evaluation judge | Gemini `3.5-flash` / `flash-lite` and Groq `gpt-oss-120b` / `20b`; DeepEval cross-check on Groq. Never DeepSeek |
| Learner model | Bayesian knowledge tracing (no neural model) |

## How it works

```mermaid
flowchart LR
    S[Videos, PDFs, slides] -->|upload| I[Ingest<br/>Whisper large-v3-turbo<br/>Gemini figure captions]
    I --> E[EmbeddingGemma 2] --> KB[(Qdrant + SQLite units<br/>page / slide / timestamp)]
    I --> CG[(Concept graph<br/>DeepSeek)]
    KB --> R[Retrieve + rerank<br/>bge-reranker-v2-m3]
    CG --> R
    R --> Chat[Grounded chat<br/>DeepSeek, cited by E markers]
    R --> Quiz[Adaptive quiz<br/>DeepSeek writes, Groq solves]
    Chat --> LM[(Learner model<br/>BKT mastery)]
    Quiz --> LM
    LM -->|depth, order, schedule| Chat
    LM --> Quiz
```

```mermaid
flowchart LR
    Q[Question] --> T[Text index<br/>EmbeddingGemma 2]
    Q --> P[Page index, optional<br/>ColSmol]
    T --> RR[Cross-encoder rerank<br/>bge-reranker-v2-m3]
    P --> RR
    RR --> G{Score >= tau?}
    G -->|no| X[Refuse, or answer as<br/>outside_syllabus]
    G -->|yes| W[Answer with E markers<br/>DeepSeek, adapted to mastery]
    W --> C[Check each sentence's marker,<br/>resolve page / slide / second]
```

```mermaid
flowchart LR
    S[Pick topics] --> B{Unseen item<br/>in bank?}
    B -->|no| G[Generate<br/>DeepSeek] --> V{Not a duplicate,<br/>blind solver agrees?<br/>Groq gpt-oss-120b}
    V -->|no| G
    V -->|yes| SV[Serve]
    B -->|yes| SV
    SV --> GR[Grade + cited feedback] --> BKT[BKT update] --> REP[Report and schedule]
```

Items can be MCQ, short answer or numeric, each tagged with topic, concept, difficulty and source location. Mastery
uses BKT (slip 0.10, guess 0.20, learn 0.05). New students take an intake question and a short IRT diagnostic.

## Run it

Copy the env file and fill in one LLM key (`DEEPSEEK_API_KEY` or `GEMINI_API_KEY`) and `HF_TOKEN`.

```bash
cp RAG_DB/.env.example RAG_DB/.env
```

**CPU** (give Docker 8 GB of memory; the first start downloads models):

```bash
docker compose -f docker-compose.app.yml --profile asr up -d --build
```

**NVIDIA GPU** (native Docker Engine with the NVIDIA Container Toolkit):

```bash
docker compose -f docker-compose.app.yml -f docker-compose.gpu.yml --profile asr up -d --build
```

UI at http://localhost:3000, API at http://localhost:8000 (`WEB_PORT` and `API_PORT` change them). Upload a PDF, deck
or video on the home page, wait for the job to reach `done`, then use chat, quizzes and the dashboard.

**Without Docker:**

```bash
cd RAG_DB && python src/pipeline.py ingest && python src/pipeline.py extract \
  && python src/pipeline.py label && python src/pipeline.py embed
cd src && LOOPERS_NO_REEXEC=1 uvicorn api.main:app   # no --reload: it orphans a running ingest job
# in another terminal:
cd frontend && npm run dev
```

**Deployed** (Vercel UI, Render API with no models, Modal GPU worker):

```mermaid
flowchart LR
    B[Browser] --> V[Vercel<br/>Next.js]
    V -->|/api| R[Render API<br/>tutor, quiz, learner]
    V -->|/media + key| W[Modal GPU worker, T4<br/>EmbeddingGemma 2, bge-reranker,<br/>Whisper, Piper, ingest]
    R -->|embed, rerank, voice,<br/>uploads, jobs| W
    W --> Q[(Qdrant Cloud)]
    R --> Q
    W --- VOL[(Modal Volume<br/>raw, processed, metadata, HF cache)]
    W -.->|knowledge tables,<br/>copied after each job| R
```

Render sends model calls and uploads to the worker when `WORKER_URL` is set and copies the worker's knowledge tables
after each job and at boot. If the worker is down, chat answers `503 GPU worker offline`. A shared `WORKER_KEY`
protects every worker route except `/api/health`. The Modal worker has a stable HTTPS URL and needs no tunnel.

| Where | Variables |
|---|---|
| Modal secret `loopers-worker` | `WORKER_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`, `DEEPSEEK_API_KEY`, `HF_TOKEN`, optional `GROQ_API_KEY`, `GEMINI_API_KEY` |
| Render | `WORKER_URL` (the Modal URL), `WORKER_KEY`, `COLLECTION`, `QDRANT_URL`, `QDRANT_API_KEY`, LLM keys, `API_CORS_ORIGINS` |
| Vercel | `API_URL` (Render), `MEDIA_URL` (the Modal URL), `WORKER_KEY` |
| GitHub Actions variables | `RENDER_API_URL` (keep-alive ping). Leave `WORKER_URL` unset: pinging Modal keeps the GPU running and billed |

Set up the worker from `RAG_DB` (`pip install modal && modal setup` first):

```bash
modal secret create loopers-worker WORKER_KEY=... QDRANT_URL=... QDRANT_API_KEY=... DEEPSEEK_API_KEY=... HF_TOKEN=...
modal volume create loopers-data
for d in metadata processed raw; do modal volume put loopers-data deploy_data/$d /$d; done   # demo course
modal deploy modal_worker.py          # WARM=1 modal deploy modal_worker.py keeps one container always on
```

`modal deploy` prints the worker URL; put it in Render's `WORKER_URL` and Vercel's `MEDIA_URL`, then redeploy Vercel
(rewrites are baked in at build time). `RAG_DB/export_deploy_data.sh` builds `deploy_data/`. The worker scales to zero
after 10 idle minutes; the first request then takes about a minute while models load. It runs one container because its
state is SQLite plus files on the volume, and it commits the volume every 20 seconds. Render's free plan resets
students, mastery and chats on every deploy; knowledge comes back from the worker.

`deploy_data/` carries metadata and files but no vectors. To put a source's vectors into Qdrant without touching other
sources, run `modal run modal_worker.py::reindex_cli --source-ids <id1>,<id2>`. A single job's stages time out after
`JOB_STAGE_TIMEOUT` seconds (3600 on Modal), and `extract` skips files over `EXTRACT_MAX_MB` (default 40) and leaves them
in place.

## Test it

```bash
cd RAG_DB && python -m pytest tests -q                                # backend
cd frontend && npm run typecheck && npm run lint && npx vitest run    # frontend
bash scripts/e2e_smoke.sh                                             # upload, chat, quiz on a running stack
RENDER_API_URL=<api> bash RAG_DB/scripts/e2e_worker.sh doc.pdf "a question it answers"   # deployed stack
```

Evaluation:

```bash
cd RAG_DB/src
python -m evaluation.run --k 5 --indexed-only          # retrieval + gate calibration
python -m evaluation.run --answers --limit N           # + faithfulness, relevancy, context P/R
python -m evaluation.simulate                          # simulated students
```

Don't run `evaluation.run` while the API is answering chats on a 6 GB GPU. To turn on page-image retrieval, start the
API with `LOOPERS_VISUAL_RETRIEVAL=1` and run `python -m ingest.page_embed`.

## Results (2026-10-13)

Demo course *Limits and Continuity*: OpenStax *Calculus Volume 1* ch. 2 (64 pages), MIT OCW 18.01 Lecture 2
(52 min) and a 12-slide deck. Run `ev_c7dd7ca45d` (`--answers --indexed-only`), 27 in-scope questions
(13 textbook, 8 video, 4 multi-hop, 2 Hindi) and 40 off-material questions, judged by Gemini Flash; simulation
`sim_3db7c2479d`, 20 students per profile over 10 sessions of 10 items:

| Metric | Value |
|---|---|
| Retrieval hit@5 / MRR | 0.93 / 0.89 |
| Refusal honesty / over-refusal | 1.00 / 0.07 |
| Faithfulness / answer relevancy | 0.75 / 0.99 |
| Context precision / recall | 0.87 / 0.81 |
| Citation accuracy | 0.57 |
| Mastery gain, adaptive vs control | 0.126 vs 0.115 (paired +0.011, SE 0.0008; adaptive better for 98% of students) |
| Question repetition, adaptive vs control | 0.001 vs 0.075 |

**Limits.** The golden set is generated and then spot-checked. The gate is calibrated on the same questions it is
scored on. The adaptive gain depends on a simulated student model. BKT drifts over sessions because it does not model
forgetting.

## Coverage of the problem statement

Checked against the code on 2026-10-14. Backend tests: 80 pass, 2 fail (`test_golden_set_valid` and one scheduler test).
Frontend: 30 vitest cases pass and `tsc` is clean.

| PS | Requirement | Status |
|---|---|---|
| 1a-1c | Ingest video, PDFs and slides; topics, concepts, prerequisites; every unit tagged and linked to page, slide or second | Done |
| 1d | Use figures and diagrams | Partial: figures are captioned and retrieved, but kept out of the refusal gate |
| 2a-2b | Cited excerpts that open the exact source; decline or flag uncovered questions | Done |
| 3a | MCQ, short and numeric quizzes and mock exams on a chosen scope, tagged | Done; a mock exam is a quiz mode (timer, deferred feedback) |
| 3b | Verified items, no repeats | Partial: blind solver done, duplicate check is fuzzy, and `/api/quiz/generate` can repeat |
| 3c | Cited feedback, weak-topic and misconception report | Partial: misconceptions only for MCQ |
| 4a | Mastery updated by quizzes and conversation | Partial: chat applies one BKT step, quizzes also update the review schedule |
| 4b | Diagnostic and intake for new students | Partial: form plus a five-question diagnostic, not a conversation |
| 5a-5c | Evaluation framework, metrics on a test set, simulated students | Done, with caveats: RAGAS definitions on our own judge calls, DeepEval as a cross-check, parametric (not LLM-played) students |
| 6a | Course flow map | Done |
| 6b | Revision material | Partial: flashcards and a cited text brief; no generated slides |
| 6c | Study schedule | Partial: exam date and review due dates, no forgetting curve drawn |
| 6d | Hindi | Partial: works when the first retrieval is refused; the chat box always sends `lang: "en"` |
| 6e | Audio tutoring | Partial: speech to text for questions, text to speech for the brief, chat answers not spoken |
| Deliverables | Prototype, documentation, evaluation results | Done |
| Deliverables | 3-10 minute demo video | Not made yet |

## Built with

Python (FastAPI, uvicorn), Next.js, Qdrant, SQLite, Docker, Modal, Render and Vercel, plus the models listed under
[Models](#models).

**AI tools used:** Claude (Claude Code) and Cline. No other AI coding assistant was used.

**AI-assisted parts.** Claude and Cline helped write and edit code across the project: the backend packages in
`RAG_DB/src/` (ingest, retrieval, tutor, quiz, learner model, evaluation), the Next.js frontend, the Modal worker and
Docker setup, the tests, and this README. The project design, requirements and decisions were ours, and we reviewed
and ran the AI-written code before keeping it.

```
RAG_DB/src/       pipeline modules + core/ api/ ingest/ concepts/ tutor/ assess/ learner/ revision/ evaluation/
RAG_DB/tests/     one small test file per package
RAG_DB/modal_worker.py   GPU worker for the deployed stack (Modal)
frontend/         Next.js web app (/, /quiz, /report, /dashboard, /eval)
whisper-server/   local transcription
```
