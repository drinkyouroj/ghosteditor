"""Pydantic models for story bible validation.

Per DECISION_004 JUDGE amendment #5: validate Claude's output against a schema,
not just json.loads(). Provides type coercion and clear error messages.
"""

from pydantic import BaseModel, Field


class PhysicalDescription(BaseModel):
    age: str | None = None
    gender: str | None = None
    appearance: str | None = None


class Relationship(BaseModel):
    to: str
    type: str


class Character(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    first_appearance: str = ""
    role: str = "minor"  # protagonist, antagonist, supporting, minor, mentioned
    traits: list[str] = Field(default_factory=list)
    physical: PhysicalDescription = Field(default_factory=PhysicalDescription)
    relationships: list[Relationship] = Field(default_factory=list)


class TimelineEvent(BaseModel):
    event: str
    chapter: int
    date_in_story: str | None = None
    characters_involved: list[str] = Field(default_factory=list)


class Setting(BaseModel):
    name: str
    description: str = ""
    chapter_introduced: int = 1


class VoiceProfile(BaseModel):
    pov: str = ""
    tense: str = ""
    tone: str = ""
    style_notes: str = ""


class PlotThread(BaseModel):
    thread: str
    status: str = "open"  # open, progressing, resolved
    introduced_chapter: int = 1
    last_updated_chapter: int = 1


class StoryBibleSchema(BaseModel):
    characters: list[Character] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    settings: list[Setting] = Field(default_factory=list)
    world_rules: list[str] = Field(default_factory=list)
    voice_profile: VoiceProfile = Field(default_factory=VoiceProfile)
    plot_threads: list[PlotThread] = Field(default_factory=list)
