"""Pydantic schema for nonfiction argument maps.

Defines the structured output format for argument map generation,
mirroring the role of StoryBibleSchema for fiction manuscripts.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class FormatConfidence(BaseModel):
    format: str = Field(description="Detected nonfiction format")
    confidence: str = Field(description="Confidence level: high, medium, low")


class VoiceProfile(BaseModel):
    register: str = Field(description="Formality level")
    pov: str = Field(description="Point of view")
    notable_patterns: list[str] = Field(default_factory=list, description="Distinctive stylistic features")


class ArgumentThread(BaseModel):
    id: str = Field(description="Short unique identifier")
    claim: str = Field(description="One-sentence statement of the argument")
    first_seen_section: int = Field(description="Section number where first introduced")
    status: str = Field(default="open", description="open, resolved, or abandoned")
    supporting_evidence_count: int = Field(default=0, description="Number of evidence items supporting this thread")


class EvidenceItem(BaseModel):
    type: str = Field(description="Type: statistic, anecdote, citation, example, analogy, expert_quote")
    summary: str = Field(description="Brief summary of the evidence")
    section: int = Field(description="Section number where found")
    supports_thread: str | None = Field(default=None, description="ID of argument thread this supports")


class StructuralMarker(BaseModel):
    type: str = Field(description="Type: introduction, conclusion, transition, digression, definition")
    section: int = Field(description="Section number")
    description: str = Field(description="Brief description of the structural element")


class ArgumentMapSchema(BaseModel):
    central_thesis: str | None = Field(default=None, description="Main argument or purpose")
    claimed_audience: str | None = Field(default=None, description="Intended reader")
    detected_format_confidence: FormatConfidence | None = Field(default=None)
    voice_profile: VoiceProfile | None = Field(default=None)
    argument_threads: list[ArgumentThread] = Field(default_factory=list)
    evidence_log: list[EvidenceItem] = Field(default_factory=list)
    structural_markers: list[StructuralMarker] = Field(default_factory=list)
