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
    lang: str = "en"


class BriefRequest(BaseModel):
    student_id: str
    concept_ids: list[str] | None = None
    lang: str = "en"


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
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}


@router.post("/sources", status_code=202)
def create_source(
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
    kind: str | None = Form(None),
    title: str | None = Form(None),
    lecture_no: int | None = Form(None),
    lang: str = Form("en"),
    subject: str = Form("general"),
):
    """Save a document under raw/<subject>/ and run ingest -> extract -> label -> embed as a job.

    Send either `file` or `url` (a YouTube or other http(s) video link).
    """
    if (file is None) == (url is None):
        raise HTTPException(422, "Send either file or url")
    if url is not None:
        return _start_url_source(url, title, lecture_no, lang)
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
    # The same content uploaded again (under any title) reuses its file: a second copy would be extracted and
    # embedded as a second document with the same source_id, duplicating every chunk.
    existing = next((runner.ROOT / "raw").rglob(f"*_{source_id[:8]}{ext}"), None)
    if existing:
        tmp.unlink()
        dest = existing
    else:
        # Never write the client's file name: sanitized title plus content hash.
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", title)[:40]
        dest = dest_dir / f"{safe}_{source_id[:8]}{ext}"
        tmp.replace(dest)

    _register_source(source_id, kind or ("video" if ext in MEDIA_EXTS else "slides" if ext == ".pptx" else "textbook"),
                     title, dest.name, str(dest.relative_to(runner.ROOT)), lecture_no, lang)
    try:
        job_id = runner.start(runner.MEDIA_STAGES if ext in MEDIA_EXTS else runner.DOCUMENT_STAGES, source_id)
    except runner.Busy:
        raise HTTPException(409, "A pipeline job is already running; the file is saved, run the pipeline later")
    return {"source_id": source_id, "job_id": job_id}


def _register_source(source_id, kind, title, file_name, media_path, lecture_no, lang) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO sources (source_id, kind, title, file_name, media_path, lecture_no, lang, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)"
            " ON CONFLICT(source_id) DO UPDATE SET status = 'running'",  # a re-upload keeps the first title
            (source_id, kind, title, file_name, media_path, lecture_no, lang, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _start_url_source(url: str, title: str | None, lecture_no: int | None, lang: str) -> dict:
    """YouTube URL: audio is downloaded, transcribed and embedded; media_path holds the URL itself."""
    from urllib.parse import urlparse
    if not re.match(r"https?://\S+$", url) or (urlparse(url).hostname or "").lower() not in YOUTUBE_HOSTS:
        raise HTTPException(422, "url must be an http(s) YouTube link (youtube.com or youtu.be)")
    if runner._busy.locked():
        raise HTTPException(409, "A pipeline job is already running")
    source_id = hashlib.sha256(url.encode()).hexdigest()[:16]
    _register_source(source_id, "video", title or url, url[:200], url, lecture_no, lang)
    try:
        return {"source_id": source_id, "job_id": runner.start(runner.MEDIA_STAGES, source_id)}
    except runner.Busy:
        raise HTTPException(409, "A pipeline job is already running; the url is saved, run the pipeline later")


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


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    """Kill a running job so a stuck one stops blocking uploads."""
    if not runner.cancel(job_id):
        raise HTTPException(404, "No running job with that id")
    return {"job_id": job_id, "status": "cancelling"}


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
        " FROM sources s WHERE s.hidden = 0 ORDER BY s.lecture_no, s.created_at"
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

@router.get("/eval/runs")
def eval_runs(kind: Literal["golden", "simulation"] | None = None, limit: int = 20):
    """Saved evaluation runs, newest first: retrieval/judge metrics ("golden") and simulated-student runs ("simulation")."""
    sql = "SELECT run_id, kind, config_json, metrics_json, created_at FROM eval_runs"
    args: tuple = ()
    if kind:
        sql, args = sql + " WHERE kind = ?", (kind,)
    rows = _rows(sql + " ORDER BY created_at DESC LIMIT ?", (*args, max(1, min(limit, 100))))
    return [{"run_id": r["run_id"], "kind": r["kind"], "created_at": r["created_at"],
             "config": json.loads(r["config_json"] or "{}"), "metrics": json.loads(r["metrics_json"] or "{}")} for r in rows]


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
        _rows(
            "SELECT uc.unit_id FROM unit_concepts uc "
            "JOIN units u ON uc.unit_id = u.unit_id "
            "JOIN sources s ON u.source_id = s.source_id "
            "WHERE uc.concept_id = ? AND (s.hidden IS NULL OR s.hidden = 0) "
            "ORDER BY uc.score DESC LIMIT 20",
            (concept_id,),
        )
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
    exam_date: str | None = None  # additive: ISO date, drives the study schedule

@router.post("/students/onboard")
def onboard(req: OnboardReq):
    theta = onboard_student(req.student_id, req.background, req.exam_date)
    return {"status": "success", "theta_0": theta}

class StudentName(BaseModel):
    name: str


@router.put("/students/{student_id}")
def put_student(student_id: str, body: StudentName):
    """Create the student if new, else rename. The id comes from the browser (no auth, TRD §11)."""
    name = body.name.strip()
    if not name or len(name) > 60:
        raise HTTPException(422, "name must be 1 to 60 characters")
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO students (student_id, name, created_at) VALUES (?, ?, ?)"
            " ON CONFLICT(student_id) DO UPDATE SET name = excluded.name",
            (student_id, name, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"student_id": student_id, "name": name}


@router.get("/students/{student_id}")
def get_student(student_id: str):
    rows = _rows("SELECT student_id, name, lang, exam_date, created_at FROM students WHERE student_id = ?", (student_id,))
    if not rows:
        raise HTTPException(404, "Student not found")
    return rows[0]


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
        rows = _rows("SELECT answer_json, type, rubric_json FROM items WHERE item_id = ?", (req.item_id,))
        if not rows:
            raise HTTPException(404, "Item not found")
        tolerance = json.loads(rows[0]["rubric_json"] or "{}").get("tolerance")
        correct = same_answer(rows[0]["type"], json.loads(rows[0]["answer_json"])[0], req.response[0] if req.response else "", tolerance)
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
            except ValueError:  # malformed last_review timestamp
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
    from learner.scheduler import plan_days

    return plan_days(student_id, minutes_per_day)


@router.get("/students/{student_id}/quizzes")
def student_quizzes(student_id: str):
    """List past quizzes and attempts for a student across all sessions and devices (Issue #18, #21)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT q.quiz_id, q.created_at,
                   COUNT(a.attempt_id) as total_items,
                   SUM(CASE WHEN a.correct = 1.0 THEN 1 ELSE 0 END) as correct_items
            FROM (
                SELECT quiz_id, student_id, created_at FROM quizzes WHERE student_id = ?
                UNION
                SELECT quiz_id, student_id, MIN(ts) as created_at FROM attempts WHERE student_id = ? GROUP BY quiz_id
            ) q
            LEFT JOIN attempts a ON q.quiz_id = a.quiz_id AND a.student_id = q.student_id
            WHERE q.student_id = ?
            GROUP BY q.quiz_id
            ORDER BY q.created_at DESC
            """,
            (student_id, student_id, student_id),
        ).fetchall()
        return [
            {
                "quiz_id": r[0],
                "created_at": r[1],
                "total_items": r[2] or 0,
                "correct_items": r[3] or 0,
                "score": round((r[3] or 0) / r[2], 3) if r[2] else 0.0,
            }
            for r in rows
        ]
    finally:
        conn.close()

# --- 6.6 voice (B6) ---
from fastapi import UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
import tempfile
import os
from voice.services import transcribe_audio, synthesize_speech

@router.post("/voice/stt")
def voice_stt(audio: UploadFile = File(...)):
    ext = Path(audio.filename or "").suffix.lower()
    if not (audio.content_type or "").startswith(("audio/", "video/")) and ext not in MEDIA_EXTS:
        raise HTTPException(415, "Send an audio or video file")
    fd, path = tempfile.mkstemp(suffix=ext or ".wav")
    size = 0
    with os.fdopen(fd, 'wb') as f:
        while chunk := audio.file.read(1 << 20):  # stream: a whole-file read can OOM the API
            size += len(chunk)
            if size > MAX_UPLOAD:
                f.close()
                os.remove(path)
                raise HTTPException(413, "File larger than 200 MB")
            f.write(chunk)

    try:
        return transcribe_audio(path)
    except Exception as e:  # server down and local model failed
        raise HTTPException(502, f"transcription failed: {e}")
    finally:
        os.remove(path)

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
from assess.generate import NoMaterial
from assess.quiz import build_quiz, make_item, public_item, quiz_student, submit_answers
from assess.verify import same_answer
from assess.grade import grade_submission, item_feedback
from assess.report import generate_report
from tutor.answer import NoProvider

class QuizGenerateReq(BaseModel):
    concept_id: str
    topic_id: str
    item_type: Literal["mcq", "short", "numeric"] = "mcq"
    stem_override: str | None = None

@router.post("/quiz/generate")
def quiz_generate(req: QuizGenerateReq):
    try:
        item, reason = make_item(req.concept_id, req.topic_id, req.item_type, req.stem_override)
    except NoMaterial:
        return {"status": "rejected", "reason": "no_material"}
    except NoProvider:
        raise HTTPException(503, "no LLM provider is configured for quiz generation")
    except (ValueError, KeyError, json.JSONDecodeError):  # generator reply was not a usable item
        return {"status": "rejected", "reason": "bad_generation"}
    if item is None:
        return {"status": "rejected", "reason": reason}
    return {"status": "success", "item": public_item(item)}


@router.post("/quizzes")
def create_quiz(req: QuizCreate):
    """A practice quiz or timed mock exam over the chosen topics/concepts with a mix of item types (TRD §6.5)."""
    if req.mode not in ("practice", "mock"):
        raise HTTPException(422, "mode must be practice or mock")
    if not 1 <= sum(req.mix.values()) <= 30 or set(req.mix) - {"mcq", "short", "numeric"}:
        raise HTTPException(422, "mix must use mcq/short/numeric with 1 to 30 items in total")
    try:
        return build_quiz(req.student_id, req.mode, req.scope.topic_ids, req.scope.concept_ids, req.mix, req.difficulty)
    except NoMaterial as e:
        raise HTTPException(404, str(e))
    except NoProvider:
        raise HTTPException(503, "no LLM provider is configured for quiz generation")


@router.post("/quizzes/{quiz_id}/answers")
def answer_quiz(quiz_id: str, body: AnswerSubmit):
    try:
        return submit_answers(quiz_id, [a.model_dump() for a in body.answers])
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/quizzes/{quiz_id}/report")
def quiz_report_by_id(quiz_id: str):
    student_id = quiz_student(quiz_id)
    if student_id is None:
        raise HTTPException(404, "Quiz not found")
    return generate_report(student_id, quiz_id)

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
    return {"status": "success", "correct": correct, "feedback": item_feedback(req.item_id)}

@router.get("/quiz/report/{session_id}")
def quiz_report(student_id: str, session_id: str):
    return generate_report(student_id, session_id)


# --- 6.6 revision (PS 6b) ---

@router.post("/revision/flashcards")
def revision_flashcards(req: FlashcardRequest):
    from revision.flashcards import flashcards

    return {"cards": flashcards(req.student_id, req.concept_ids, req.n, req.lang)}


@router.post("/revision/brief")
def revision_brief(req: BriefRequest):
    from revision.brief import brief

    return brief(req.student_id, req.concept_ids, req.lang)
