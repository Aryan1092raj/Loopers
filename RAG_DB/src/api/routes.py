"""Every endpoint in TRD §6 under /api.

Real now: chat (retrieval, score gate, stored citations), sources list, jobs, graph,
concepts, citations. The rest serve `frontend/mocks/*.json` or return 501 until their
owner lands them (B: students, quizzes, voice; C: revision, eval).
"""
import json
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

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

@router.post("/sources", status_code=202)
def create_source(
    file: UploadFile = File(...),
    kind: str | None = Form(None),
    title: str | None = Form(None),
    lecture_no: int | None = Form(None),
    lang: str = Form("en"),
):
    _todo()  # A6 (documents), B1 (video); JSON {url, kind} variant arrives with B1


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

@router.post("/students")
def create_student(body: StudentCreate):
    _todo()


@router.post("/students/{student_id}/intake")
def intake(student_id: str, body: IntakeMessage):
    _todo()


@router.get("/students/{student_id}/mastery")
def get_mastery(student_id: str):
    return _mock("mastery")


@router.get("/students/{student_id}/schedule")
def get_schedule(student_id: str, minutes_per_day: int = 60):
    return _mock("schedule")


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
