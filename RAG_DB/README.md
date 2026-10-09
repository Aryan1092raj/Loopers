# RAG_DB — AI-Powered RAG Pipeline for Education

Ingest textbooks (PDF, images, audio) into a searchable vector knowledge base,
then generate exam-ready teaching materials — LaTeX Beamer slide decks, study
notes, and assignments — on any topic via RAG.

Currently loaded with ~150+ documents from NCERT (classes 9–12) and OpenStax
textbooks across 17 subjects, plus a Linear Algebra reference.

## Pipeline

```
                               ┌─ MinerU (STEM)
                    ┌─ PDF ────┼─ Docling (Humanities)
raw/ ──┬─ audio ────┤          └─ PP-StructureV3 (fallback)
       │            └─ image ──→ PaddleOCR
       └─ download (OpenStax / NCERT)
            │
            ▼
    processed/*.md ──→ labels.json ──→ qdrant_db/ ──→ output/*.pdf
         [2]              [3]              [4]            [5]
                                             └─ eval: retrieval hit@k
```

| Phase | Script | What it does |
|---|---|---|
| 0 Download | `corpus_downloader.py` | Download open-access textbooks (OpenStax, NCERT). |
| 1 Ingest | `ingest.py` | Registers raw files (sha256, type, subject). Idempotent. |
| 2 Extract | `extract.py` | Routes STEM→MinerU, Humanities→Docling, falls back PP-StructureV3. Rips figures, tables, equations. Writes `.md`. Parallel. |
| 3 Label | `label.py` | Gemini Vision captions/classifies figures. Falls back NVIDIA NIM → local GPU. Transcribes handwriting. |
| 4 Embed | `embed.py` | BGE-M3 dense + sparse vectors, Qdrant hybrid collection. Cross-Encoder reranker. Incremental (tracks sha256). |
| 5 Generate | `generate.py` + `render_latex.py` | RAG → slide-spec JSON → LaTeX Beamer PDF (with TikZ). Parallel content writers. |
| Notes | `notes.py` + `render_notes.py` | RAG → study notes PDF (5+ sections, examples, diagrams). |
| Assignment | `assignment.py` + `render_notes.py` | RAG → MCQ/short/long questions + model solutions. Student & teacher PDFs. |
| Eval | `eval.py` | Retrieval hit@k over eval questions. Optional LLM judge scoring. |

## Stack

| Layer | Technology |
|---|---|
| **OCR / Layout** | PaddleOCR (PP-StructureV3), MinerU, Docling, GLM-OCR, PyMuPDF |
| **Embeddings** | BGE-M3 (1024-dim dense + BM25 sparse) via `FlagEmbedding`, GPU fp16 |
| **Vector DB** | Qdrant — embedded mode (default) or Docker server. Hybrid dense+sparse, INT8 scalar quantization, RRF fusion |
| **LLM — generation** | AgentRouter (Claude Opus proxy), Groq (4-key pool: 3 content + 1 TikZ), OpenAI, Gemini, NVIDIA NIM |
| **LLM — vision** | Gemini Vision, NVIDIA NIM vision, Ollama Moondream + PaddleOCR (local GPU) |
| **LLM — eval** | Groq (Llama 3.3 70B) |
| **Audio** | faster-whisper (local), Groq Whisper API, Granite Speech, yt-dlp |
| **Knowledge Graph** | NetworkX DiGraph + SQLite, PathRAG-style weighted BFS |
| **Reranking** | BAAI/bge-reranker-v2-m3 (Cross-Encoder) |
| **Output** | LaTeX Beamer (xelatex) via custom renderer, TikZ diagrams, python-pptx, matplotlib |
| **Dashboard** | Python stdlib `http.server` (single-file, zero framework deps) |
| **Corpus sources** | OpenStax (CC-BY), NCERT (Indian curriculum, classes 6–12) |

All heavy compute (OCR, embeddings) runs locally on GPU (RTX 4050 6GB, strict
VRAM management with auto-growth allocator and fp16 inference). Cloud LLMs only
for generation and vision labeling.

## Project structure

```
├── raw/                     # Source files by subject (raw/math/, raw/physics/, …)
├── processed/               # Extracted markdown, images, formulas, tables
│   ├── images/
│   ├── formulas/
│   ├── tables/
│   └── concepts/            # Transcript-derived concepts (review queue)
├── metadata/                # Pipeline state (registry, labels, eval, clusters, KG)
├── qdrant_db/               # Local Qdrant vector store (embedded mode, committed in git)
├── qdrant_storage/          # Qdrant Docker server storage (optional)
├── output/                  # Generated PDFs, PPTXs, JSON specs
├── src/                     # All source code
│   ├── pipeline.py          # CLI entry point
│   ├── config.py            # Shared paths, env vars, subject mapping, LaTeX sanitizer
│   ├── store.py             # Qdrant client (hybrid dense+sparse, RRF)
│   ├── llm.py               # Unified LLM client (Groq/Gemini/OpenAI/NVIDIA/AgentRouter)
│   ├── embeddings.py        # BGE-M3 encoder (dense + sparse)
│   ├── ingest.py / extract.py / label.py / embed.py / generate.py
│   ├── notes.py / assignment.py / chat.py
│   ├── knowledge_graph.py   # PathRAG prerequisite graph
│   ├── corpus_downloader.py # OpenStax + NCERT textbook downloader
│   ├── render_latex.py      # LaTeX Beamer PDF renderer
│   └── parsers/             # Router + MinerU/Docling/GLM-OCR parsers
├── templates/
│   └── slide_spec.schema.json
├── .env.example
└── requirements.txt
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # pulls torch + paddle (large, one-time)

cp .env.example .env                    # add API keys
```

Required in `.env` for full functionality:
- `GROQ_API_KEY` — comma-separated list of up to 4 keys (generation, TikZ, eval)
- `GEMINI_API_KEY` — vision labeling
- `NVIDIA_API_KEY` — comma-separated fallback keys
- `AGENTROUTER_API_KEY` — AgentRouter proxy (Claude Opus)
- `OPENAI_API_KEY` — OpenAI passthrough

First `extract`/`embed` run downloads PaddleOCR + BGE-M3 models (~2.2 GB).
Expect a slow first run, fast after.

> [!NOTE]
> The pre-built vector database is committed under `qdrant_db/`. You can query and generate materials immediately without running ingestion or embedding steps for existing texts.

## Usage

```bash
# --- Download textbooks ---
python src/pipeline.py download --source ncert --subjects physics chemistry --classes 11 12
python src/pipeline.py download --source openstax --subjects all

# --- Full pipeline on a topic ---
python src/pipeline.py all "gaussian elimination" --subject math

# --- Individual phases ---
python src/pipeline.py ingest
python src/pipeline.py extract                     # parallel, downloads models first run
python src/pipeline.py label                       # Gemini Vision (needs GEMINI_API_KEY)
python src/pipeline.py embed                       # incremental (BGE-M3 GPU)
python src/pipeline.py embed --full                # force full rebuild
python src/pipeline.py embed --reset-collection    # migrate to hybrid schema

# --- Measure retrieval BEFORE trusting generation ---
python src/pipeline.py eval                        # hit@5 (default)
python src/pipeline.py eval --k 3 --judge          # + LLM scored answers

# --- Generate teaching materials ---
python src/pipeline.py generate "explain backpropagation" --subject math
python src/pipeline.py notes "photosynthesis" --subject biology
python src/pipeline.py assignment "quadratic equations" --subject math

# --- Bulk add a subject (download → ingest → extract → label → embed) ---
python src/pipeline.py add-subject --source ncert --subjects science --target-subject science --classes 10

# --- API + web console (from RAG_DB/src) ---
LOOPERS_NO_REEXEC=1 uvicorn api.main:app --reload   # http://localhost:8000
```

Subject is auto-detected from file location (`raw/math/` → "mathematics"), or
overridden with `--subject`. 17 canonical subjects supported.

### Multi-subject

Files in `raw/math/chapter1.pdf` → subject "mathematics".
Loose files in `raw/` → subject "general".
Subject-scoped retrieval: `generate "query" --subject math` only searches that
subject's chunks.

## API and web console

`uvicorn api.main:app` serves the FastAPI app and a static console at `localhost:8000`.
Upload documents, video or audio as ingest jobs, tail job logs, cancel a stuck job
(`POST /api/jobs/{job_id}/cancel`), chat with the tutor, and take quizzes.

## Extra capabilities

- **Audio pipeline**: YouTube download (yt-dlp), ffmpeg denoising, faster-whisper/Groq/Granite transcription
- **Knowledge graph**: PathRAG-style prerequisite extraction, SQLite persistence, weighted BFS traversal
- **CLAF levels**: Cognitive Level Adaptive Framework — basic/intermediate/advanced tagging per chunk
- **Eval harness**: hit@k with configurable k, LLM judge for answer quality scoring
- **Hybrid retrieval**: Dense (BGE-M3) + sparse (BM25) fused via RRF, reranked by Cross-Encoder
- **Parallel generation**: Up to 8 concurrent LLM calls for slide content, grouped by section boundary
- **Subject-specific prompts**: Math (theorems, TikZ), Physics (FBD, circuits), Chemistry (energy profiles), Biology (cell diagrams), CS (flowcharts)

## GPU notes

Runs on RTX 4050 (6GB VRAM) with strict memory management:
- PaddlePaddle `auto_growth` allocator, 10% VRAM cap
- Sequential processing (one model at a time)
- fp16 inference for BGE-M3
