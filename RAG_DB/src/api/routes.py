"""Every endpoint in TRD §6 under /api.

Real now: chat (retrieval, two-tier gate, cited answers, SQLite sessions), document upload and the
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
from core.models import Citation, TutorResponse
from tutor.cite import resolve

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
MEDIA_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".mp3", ".wav", ".m4a"}  # video and audio lectures
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
    if ext not in DOC_EXTS | MEDIA_EXTS:
        raise HTTPException(415, f"Unsupported file type {ext or '(none)'}; allowed: {sorted(DOC_EXTS | MEDIA_EXTS)}")
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
            (source_id, kind or ("video" if ext in MEDIA_EXTS else "slides" if ext == ".pptx" else "textbook"), title, dest.name,
             str(dest.relative_to(runner.ROOT)), lecture_no, lang, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        job_id = runner.start(runner.MEDIA_STAGES if ext in MEDIA_EXTS else runner.DOCUMENT_STAGES, source_id)
    except runner.Busy:
        raise HTTPException(409, "A pipeline job is already running; the file is saved, run the pipeline later")
    return {"source_id": source_id, "job_id": job_id}


class PipelineRun(BaseModel):
    stages: list[Literal["ingest", "extract", "label", "embed", "concepts", "eval"]]


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
    g = concepts.get_graph(fixture=False)
    known = {}
    if student_id:
        known = {r["concept_id"]: r["p_mastery"] for r in _rows("SELECT concept_id, p_mastery FROM learner_state WHERE student_id = ?", (student_id,))}
    nodes = [
        {"id": n["id"], "name": n["name"], "level": n["level"], "parent_id": n["parent_id"], "p_know": known.get(n["id"], DEFAULT_P_KNOW)}
        for n in g["nodes"]
    ]
    return {"nodes": nodes, "edges": g["edges"]}


@router.get("/concepts/{concept_id}")
def get_concept(concept_id: str):
    concept = concepts.get_concept(concept_id)
    if concept is None:
        raise HTTPException(404, "Concept not found")
    unit_ids = [
        r["unit_id"] for r in
        _rows("SELECT unit_id FROM unit_concepts WHERE concept_id = ? ORDER BY score DESC LIMIT 20", (concept_id,))
    ]
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

@router.post("/chat", response_model=TutorResponse)
def chat(req: ChatRequest):
    from tutor.answer import respond  # heavy: retrieval loads BGE-M3, the reranker and Qdrant on first call

    return respond(
        req.message, req.student_id, req.session_id, req.scope.model_dump(), req.lang, req.allow_outside,
    )


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
    try:
        item = get_next_diagnostic_item(student_id)
    except ValueError as e:  # student not onboarded
        raise HTTPException(404, str(e))
    if not item:
        return {"status": "complete"}
    return item

class RespondReq(BaseModel):
    item_id: str
    correct: bool | None = None
    response: list[str] | None = None  # additive: the server grades it, so the client never needs the key

@router.post("/students/{student_id}/diagnostic/respond")
def respond_diagnostic(student_id: str, req: RespondReq):
    correct = req.correct
    if req.response is not None:
        rows = _rows("SELECT answer_json FROM items WHERE item_id = ?", (req.item_id,))
        if not rows:
            raise HTTPException(404, "Item not found")
        correct = sorted(req.response) == sorted(json.loads(rows[0]["answer_json"]))
    if correct is None:
        raise HTTPException(422, "Give response or correct")
    try:
        new_theta = process_diagnostic_response(student_id, req.item_id, correct)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"status": "success", "new_theta": new_theta, "correct": correct}

@router.get("/students/{student_id}/mastery")
def get_mastery(student_id: str):
    conn = get_connection()
    rows = conn.execute('''
        SELECT ls.concept_id, ls.p_mastery, ls.stability, ls.last_reviewed,
               (SELECT COUNT(*) FROM attempts a JOIN items i ON a.item_id=i.item_id WHERE a.student_id=ls.student_id AND i.concept_id=ls.concept_id) as n_obs
        FROM learner_state ls WHERE ls.student_id=?
    ''', (student_id,)).fetchall()
    conn.close()
    
    result = []
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc)
    for r in rows:
        cid, p, s, last_rev, nobs = r
        r_val = 0.0
        if last_rev:
            try:
                lr_dt = datetime.datetime.fromisoformat(last_rev)
                if lr_dt.tzinfo is None:
                    lr_dt = lr_dt.replace(tzinfo=datetime.timezone.utc)
                t = max((now - lr_dt).total_seconds() / 86400, 0)
                r_val = (9 * s) / (9 * s + t) if s > 0 else 0.0
            except:
                pass
                
        result.append({
            "concept_id": cid,
            "p_know": p,
            "retrievability": round(r_val, 3),
            "n_obs": nobs or 0,
            "last_review": last_rev
        })
    return result

@router.get("/students/{student_id}/schedule")
def get_schedule(student_id: str, minutes_per_day: int = 60):
    from learner.scheduler import get_next_schedule
    import datetime
    
    sched = get_next_schedule(student_id, limit=10)
    today = datetime.date.today().isoformat()
    
    tasks = []
    for item in sched:
        tasks.append({
            "concept_id": item["concept_id"],
            "minutes": 15,
            "reason": "retrievability low" if item.get("is_overdue") else "mastery gap"
        })
        
    return [
        {
            "date": today,
            "tasks": tasks
        }
    ]

# --- 6.6 voice (B6) ---
from fastapi import UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
import tempfile
import os
from voice.services import transcribe_audio, synthesize_speech

@router.post("/voice/stt")
def voice_stt(audio: UploadFile = File(...)):
    # Write to temp file
    fd, path = tempfile.mkstemp(suffix=".wav")
    with os.fdopen(fd, 'wb') as f:
        f.write(audio.file.read())
        
    try:
        res = transcribe_audio(path)
    finally:
        os.remove(path)
        
    return res

class TTSRequest(BaseModel):
    text: str

@router.post("/voice/tts")
def voice_tts(req: TTSRequest):
    from config import PROCESSED
    out_dir = PROCESSED / "media" / "tts_cache"  # was an absolute path from another machine
    path = synthesize_speech(req.text, out_dir)
    return FileResponse(path, media_type="audio/wav")


# --- 6.5 quizzes (B5) ---
from pydantic import BaseModel
from assess.generate import generate_item
from assess.dedup import check_duplicate
from assess.verify import blind_verify
from assess.grade import grade_submission
from assess.report import generate_report

class QuizGenerateReq(BaseModel):
    concept_id: str
    topic_id: str
    stem_override: str | None = None
    mock_solver_answer: str | None = None

@router.post("/quiz/generate")
def quiz_generate(req: QuizGenerateReq):
    item = generate_item(req.concept_id, req.topic_id, req.stem_override)
    
    # Dedup
    if check_duplicate(req.concept_id, item["stem"]):
        return {"status": "rejected", "reason": "duplicate"}
        
    # Verify
    if not blind_verify(item["stem"], item["options_json"], item["answer_json"], req.mock_solver_answer):
        return {"status": "rejected", "reason": "solver_conflict"}
        
    # Commit item
    conn = get_connection()
    conn.execute(
        "INSERT INTO items (item_id, concept_id, topic_id, type, bloom, stem, options_json, answer_json, rubric_json, unit_ids_json, difficulty_b, discrimination_a, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (item["item_id"], item["concept_id"], item["topic_id"], item["type"], item["bloom"], item["stem"], item["options_json"], item["answer_json"], item["rubric_json"], item["unit_ids_json"], item["difficulty_b"], item["discrimination_a"])
    )
    conn.commit()
    conn.close()
    
    return {"status": "success", "item": item}

class QuizSubmitReq(BaseModel):
    quiz_id: str
    item_id: str
    student_answer: list[str]

@router.post("/quiz/submit")
def quiz_submit(student_id: str, req: QuizSubmitReq):
    try:
        correct = grade_submission(student_id, req.quiz_id, req.item_id, req.student_answer)
    except ValueError as e:  # unknown item
        raise HTTPException(404, str(e))
    return {"status": "success", "correct": correct}

@router.get("/quiz/report/{session_id}")
def quiz_report(student_id: str, session_id: str):
    return generate_report(student_id, session_id)
