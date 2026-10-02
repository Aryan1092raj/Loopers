from typing import Literal
from pydantic import BaseModel, Field

SourceType = Literal["video", "slides", "textbook"]
UnitKind = Literal["text", "figure", "transcript"]
Mode = Literal["grounded", "partially_grounded", "refused", "outside_syllabus"]

class Locator(BaseModel):
    page_no: int | None = None
    slide_no: int | None = None
    t_start: float | None = None          # seconds
    t_end: float | None = None
    bbox: list[float] | None = None       # [x0, y0, x1, y1] in page-image pixels
    matched_slide_page: int | None = None # for video units

class SourceUnit(BaseModel):
    unit_id: str
    source_id: str
    source_type: SourceType
    kind: UnitKind
    title: str                            # lecture or chapter title
    heading: str | None = None
    text: str
    parent_text: str | None = None
    locator: Locator
    image_path: str | None = None         # figure crop or keyframe
    latex: list[str] = []
    lang: str = "en"
    topic_id: str | None = None
    concept_ids: list[str] = []
    has_math: bool = False

class Evidence(BaseModel):
    id: str                               # "E1", "E2", ...
    unit: SourceUnit
    score: float                          # reranker score in [0, 1]
    is_prerequisite: bool = False

class Citation(BaseModel):
    id: str                               # matches the marker in the answer
    unit_id: str
    source_id: str
    source_type: SourceType
    title: str
    locator: Locator
    seek_url: str | None = None           # "/media/video/lec04.mp4#t=1425.2"
    page_image_url: str | None = None     # "/media/pages/lec04_slides/18.webp"
    thumb_url: str | None = None
    excerpt: str

class GateResult(BaseModel):
    top_score: float
    tier: Literal[1, 2] | None = None
    band: Literal["low", "borderline", "high"]
    nli: Literal["ENTAILMENT", "NEUTRAL", "CONTRADICTION"] | None = None
    decision: Literal["answer", "refuse"]

class Segment(BaseModel):
    text: str
    evidence_ids: list[str] = []
    outside: bool = False

class Refusal(BaseModel):
    reason: Literal["out_of_material", "not_entailed"]
    message: str
    offer_outside: bool = True

class MasteryUpdate(BaseModel):
    concept_id: str
    p_before: float
    p_after: float

class TutorResponse(BaseModel):
    mode: Mode
    answer: str
    segments: list[Segment]
    citations: list[Citation]
    gate: GateResult
    refusal: Refusal | None = None
    mastery_updates: list[MasteryUpdate] = []

class Item(BaseModel):
    item_id: str
    type: Literal["mcq", "short", "numeric"]
    concept_id: str
    topic_id: str
    bloom: Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]
    stem: str
    options: list[str] | None = None
    difficulty_b: float = Field(ge=-3.0, le=3.0)
    discrimination_a: float = Field(default=1.0, ge=0.5, le=2.5)
    unit_ids: list[str]                   # sources used for feedback citations
    verified: bool = False

class MasteryState(BaseModel):
    concept_id: str
    p_know: float
    retrievability: float | None = None
    n_obs: int = 0
    last_review: str | None = None
