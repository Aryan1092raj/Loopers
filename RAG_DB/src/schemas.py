"""Pydantic schemas for structured LLM outputs.

PresentationPlan / PptxSlide enforce McKinsey-style brevity rules:
  - ≤ 4 bullets per slide
  - ≤ 12 words per bullet
  - All heavy content goes into speaker_notes
"""
from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field, field_validator


class PptxSlide(BaseModel):
    title: str = Field(
        description="Slide title. Maximum 6 words. Use a verb or key concept phrase."
    )
    bullets: List[str] = Field(
        description=(
            "Exactly 3–4 bullet points visible on the slide canvas. "
            "STRICT RULE: maximum 12 words per bullet. No full sentences. "
            "Punchy, scannable from the back of a classroom."
        )
    )
    speaker_notes: str = Field(
        description=(
            "Full textbook explanation the teacher reads aloud. Include all "
            "derivations, formulas, worked examples, and proofs here — "
            "NOT on the slide. Minimum 3 sentences."
        )
    )

    @field_validator("bullets")
    @classmethod
    def cap_bullets(cls, v: List[str]) -> List[str]:
        """Silently drop bullets beyond the 4-point limit."""
        return [b.strip() for b in v[:4] if b.strip()]

    @field_validator("title")
    @classmethod
    def trim_title(cls, v: str) -> str:
        return v.strip()[:120]


class PresentationPlan(BaseModel):
    presentation_title: str = Field(
        description="Main title of the full presentation. Max 8 words."
    )
    subject: str = Field(
        description="Academic subject, e.g. 'Mathematics', 'Physics', 'Computer Science'."
    )
    slides: List[PptxSlide] = Field(
        description=(
            "Sequential list of 8–14 content slides (excluding title slide). "
            "Each must cover a distinct sub-topic or concept."
        )
    )

    @field_validator("slides")
    @classmethod
    def cap_slides(cls, v: List[PptxSlide]) -> List[PptxSlide]:
        return v[:16]  # hard cap — prevents runaway generation

    def to_dict(self) -> dict:
        return self.model_dump()
