"""Every endpoint in TRD §6 under /api.

Real now: chat (retrieval, score gate, stored citations), document upload and the
pipeline runner (plus job logs), sources list, jobs, graph, concepts, citations. The rest serve `frontend/mocks/*.json` or return 501 until their
owner lands them (B: students, quizzes, voice; C: revision, eval).
"""
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from api import runner
from concepts import api as concepts
from core.db import get_connection
from core.models import Citation, Refusal, Segment, TutorResponse
from tutor.cite import citation_from_payload, resolve
from tutor.gate import decide

router = APIRouter(prefix="/api")

MOCKS = Path(__file__).resolve().parents[3] / "frontend" / "mocks"
DEFAULT_P_KNOW = 0.15  # learner.state default (PLAN §4.2) until B3 lands


def _mock(name: str):
    return json.loads((MOCKS / f"{name}.json").read_text())


def _todo():
    raise HTTPException(501, "Not implemented yet")


def _rows(sql: str, args: tuple = ()) -> list[dict]:
    conn = get_connection()
    conn.row_factory = lambda cur, row: {d[0]: v for d, v in zip(cur.description, row)}
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


# --- request bodies (API-only; shared contracts stay in core/models.py) ---

class Scope(BaseModel):
    topic_ids: list[str] = []
    concept_ids: list[str] = []


class ChatRequest(BaseModel):
    session_id: str
    student_id: str
    message: str
    scope: Scope = Scope()
    lang: str = "en"
    allow_outside: bool = False


class SourceUrl(BaseModel):
    url: str
    kind: str = "video"


class StudentCreate(BaseModel):
    name: str
    lang: str = "en"
    exam_date: str | None = None


class IntakeMessage(BaseModel):
    message: str


class QuizCreate(BaseModel):
    student_id: str
    mode: str = "practice"
    scope: Scope = Scope()
    mix: dict[str, int] = {"mcq": 4, "short": 2, "numeric": 2}
    difficulty: float | None = None


class Answer(BaseModel):
    item_id: str
    response: str
    latency_ms: int | None = None


class AnswerSubmit(BaseModel):
    answers: list[Answer]


class FlashcardRequest(BaseModel):
    student_id: str
    concept_ids: list[str] | None = None
    n: int = 10


class TtsRequest(BaseModel):
    text: str
    lang: str = "en"


class EvalRunRequest(BaseModel):
    golden_path: str
    frameworks: list[str] = ["ragas", "deepeval"]
    limit: int | None = None


class SimulateRequest(BaseModel):
    personas: list[str]
    sessions: int = 10
    arm: str = "adaptive"


# --- 6.1 sources and jobs ---

DOC_EXTS = {".pdf", ".pptx", ".docx", ".md", ".txt"}
MAX_UPLOAD = 200 * 1024 * 1024


@router.post("/sources", status_code=202)
def create_source(
    file: UploadFile = File(...),
    kind: str | None = Form(None),
    title: str | None = Form(None),
    lecture_no: int | None = Form(None),
    lang: str = Form("en"),
    subject: str = Form("general"),
):
    """Save a document under raw/<subject>/ and run ingest -> extract -> label -> embed as a job.

    Videos and the JSON {url, kind} variant arrive with B1.
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in DOC_EXTS:
        raise HTTPException(415, f"Unsupported file type {ext or '(none)'}; allowed: {sorted(DOC_EXTS)}")
    if runner._busy.locked():
        raise HTTPException(409, "A pipeline job is already running")
    title = title or Path(file.filename).stem
    subject = re.sub(r"[^a-z0-9_-]", "_", subject.lower()) or "general"
    dest_dir = runner.ROOT / "raw" / subject
    dest_dir.mkdir(parents=True, exist_ok=True)

    tmp = dest_dir / f".upload_{uuid.uuid4().hex}"
    digest, size = hashlib.sha256(), 0
    with open(tmp, "wb") as out:
        while chunk := file.file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD:
                out.close()
                tmp.unlink()
                raise HTTPException(413, "File larger than 200 MB")
            digest.update(chunk)
            out.write(chunk)
    source_id = digest.hexdigest()[:16]
    # Never write the client's file name: sanitized title plus content hash.
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", title)[:40]
    dest = dest_dir / f"{safe}_{source_id[:8]}{ext}"
    tmp.replace(dest)

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO sources (source_id, kind, title, file_name, media_path, lecture_no, lang, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)"
            " ON CONFLICT(source_id) DO UPDATE SET status = 'running', title = excluded.title",
            (source_id, kind or ("slides" if ext == ".pptx" else "textbook"), title, dest.name,
             str(dest.relative_to(runner.ROOT)), lecture_no, lang, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        job_id = runner.start(runner.DOCUMENT_STAGES, source_id)
    except runner.Busy:
        raise HTTPException(409, "A pipeline job is already running; the file is saved, run the pipeline later")
    return {"source_id": source_id, "job_id": job_id}


class PipelineRun(BaseModel):
    stages: list[Literal["ingest", "extract", "label", "embed", "eval"]]


@router.post("/pipeline", status_code=202)
def run_pipeline(body: PipelineRun):
    """Run pipeline.py stages over everything in raw/ as one job (dev console)."""
    if not body.stages:
        raise HTTPException(422, "No stages given")
    try:
        return {"job_id": runner.start(body.stages)}
    except runner.Busy:
        raise HTTPException(409, "A pipeline job is already running")


@router.get("/jobs/{job_id}/log")
def get_job_log(job_id: str, tail: int = 200):
    path = runner.log_path(job_id)
    if not re.fullmatch(r"j_[0-9a-f]{12}", job_id) or not path.is_file():
        raise HTTPException(404, "Log not found")
    return {"lines": path.read_text(errors="replace").splitlines()[-tail:]}


@router.get("/sources")
def list_sources():
    return _rows(
        "SELECT s.*, (SELECT COUNT(*) FROM units u WHERE u.source_id = s.source_id) AS unit_count"
        " FROM sources s ORDER BY s.lecture_no, s.created_at"
    )


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    rows = _rows("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
    if not rows:
        raise HTTPException(404, "Job not found")
    job = rows[0]
    # jobs has no status column (TRD §5.1); derive it from the stage
    job["status"] = {"initialized": "queued", "done": "done", "failed": "failed"}.get(job["stage"], "running")
    return job


# --- 6.2 knowledge ---

@router.get("/graph")
def get_graph(student_id: str | None = None):
    g = concepts.get_graph()
    nodes = [
        {"id": n["id"], "name": n["name"], "level": n["level"], "parent_id": n["parent_id"], "p_know": DEFAULT_P_KNOW}
        for n in g["nodes"]
    ]
    return {"nodes": nodes, "edges": g["edges"]}


@router.get("/concepts/{concept_id}")
def get_concept(concept_id: str):
    concept = concepts.get_concept(concept_id)
    if concept is None:
        raise HTTPException(404, "Concept not found")
    unit_ids = [r["unit_id"] for r in _rows("SELECT unit_id FROM unit_concepts WHERE concept_id = ?", (concept_id,))]
    return {
        "concept": concept,
        "prerequisites": concepts.prerequisites(concept_id),
        "dependents": concepts.dependents(concept_id),
        "units": resolve(unit_ids),
    }


@router.get("/citations/{unit_id}", response_model=Citation)
def get_citation(unit_id: str):
    found = resolve([unit_id])
    if not found:
        raise HTTPException(404, "Unit not found")
    return found[0]


# --- 6.3 chat ---

REFUSAL_MESSAGE = "I could not find this in your course material."


@router.post("/chat", response_model=TutorResponse)
def chat(req: ChatRequest):
    from generate import retrieve  # heavy: loads BGE-M3, reranker and Qdrant on first call

    chunks, _ = retrieve(req.message)
    # Gate on reranked text scores only; image and prerequisite chunks carry other score scales.
    text_scores = [c.get("score", 0.0) for c in chunks if c.get("kind") == "text" and not c.get("_is_prerequisite")]
    gate = decide(max(text_scores, default=0.0))
    if gate.decision == "refuse":
        return TutorResponse(
            mode="refused", answer="", segments=[], citations=[], gate=gate,
            refusal=Refusal(reason="out_of_material", message=REFUSAL_MESSAGE, offer_outside=True),
        )
    citations = [citation_from_payload(f"E{i}", c) for i, c in enumerate(chunks, 1)]
    # ponytail: stub answer quotes the evidence; A5 replaces it with generation + marker validation
    segments = [Segment(text=c.excerpt, evidence_ids=[c.id]) for c in citations]
    answer = " ".join(f"{s.text} [{s.evidence_ids[0]}]" for s in segments)
    return TutorResponse(mode="grounded", answer=answer, segments=segments, citations=citations, gate=gate)


# --- 6.4 students (B4, B3, B6) ---
from pydantic import BaseModel
from learner.cold_start import onboard_student, get_next_diagnostic_item, process_diagnostic_response
from learner.state import get_learner_state

class OnboardReq(BaseModel):
    student_id: str
    background: str

@router.post("/students/onboard")
def onboard(req: OnboardReq):
    theta = onboard_student(req.student_id, req.background)
    return {"status": "success", "theta_0": theta}

@router.get("/students/{student_id}/diagnostic")
def get_diagnostic(student_id: str):
    item = get_next_diagnostic_item(student_id)
    if not item:
        return {"status": "complete"}
    return item

class RespondReq(BaseModel):
    item_id: str
    correct: bool

@router.post("/students/{student_id}/diagnostic/respond")
def respond_diagnostic(student_id: str, req: RespondReq):
    new_theta = process_diagnostic_response(student_id, req.item_id, req.correct)
    return {"status": "success", "new_theta": new_theta}

@router.get("/students/{student_id}/mastery")
def get_mastery(student_id: str):
    conn = get_connection()
    rows = conn.execute("SELECT concept_id, p_mastery, stability, difficulty, last_reviewed, next_review FROM learner_state WHERE student_id=?", (student_id,)).fetchall()
    conn.close()
    return [{
        "concept_id": r[0],
        "p_mastery": r[1],
        "stability": r[2],
        "difficulty": r[3],
        "last_reviewed": r[4],
        "next_review": r[5]
    } for r in rows]

# --- 6.5 quizzes (B5) ---

@router.post("/quizzes")
def create_quiz(body: QuizCreate):
    return _mock("quiz_q1")


@router.post("/quizzes/{quiz_id}/answers")
def submit_answers(quiz_id: str, body: AnswerSubmit):
    _todo()


@router.get("/quizzes/{quiz_id}/report")
def quiz_report(quiz_id: str):
    return _mock("quiz_report_q1")


# --- 6.6 revision, voice, evaluation ---

@router.post("/revision/flashcards")
def flashcards(body: FlashcardRequest):
    _todo()


@router.post("/revision/brief")
def revision_brief(body: FlashcardRequest):
    _todo()


@router.post("/voice/stt")
def stt(audio: UploadFile = File(...)):
    _todo()


@router.post("/voice/tts")
def tts(body: TtsRequest):
    _todo()


@router.post("/eval/run")
def eval_run(body: EvalRunRequest):
    _todo()


@router.post("/eval/simulate")
def eval_simulate(body: SimulateRequest):
    _todo()


@router.get("/eval/runs/{run_id}")
def eval_status(run_id: str):
    _todo()
