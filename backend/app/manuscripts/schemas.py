from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class ManuscriptResponse(BaseModel):
    id: uuid.UUID
    title: str
    genre: str | None
    status: str
    payment_status: str
    chapter_count: int | None
    word_count_est: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ManuscriptDetailResponse(ManuscriptResponse):
    chapters: list["ChapterSummary"]


class ChapterSummary(BaseModel):
    id: uuid.UUID
    chapter_number: int
    title: str | None
    word_count: int | None
    status: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    manuscript_id: uuid.UUID
    status: str
    job_id: uuid.UUID


class JobResponse(BaseModel):
    id: uuid.UUID
    status: str
    progress_pct: int
    current_step: str | None
    error_message: str | None

    model_config = {"from_attributes": True}
