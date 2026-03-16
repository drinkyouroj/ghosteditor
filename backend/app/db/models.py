from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# --- ENUMS ---


class SubscriptionStatus(str, enum.Enum):
    free = "free"
    per_use = "per_use"
    subscribed = "subscribed"


class ManuscriptStatus(str, enum.Enum):
    uploading = "uploading"
    extracting = "extracting"
    bible_generating = "bible_generating"
    bible_complete = "bible_complete"
    analyzing = "analyzing"
    complete = "complete"
    error = "error"


class PaymentStatus(str, enum.Enum):
    unpaid = "unpaid"
    paid = "paid"
    refunded = "refunded"


class ChapterStatus(str, enum.Enum):
    uploaded = "uploaded"
    extracting = "extracting"
    extracted = "extracted"
    analyzing = "analyzing"
    analyzed = "analyzed"
    error = "error"


class JobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class JobType(str, enum.Enum):
    text_extraction = "text_extraction"
    story_bible_generation = "story_bible_generation"
    chapter_analysis = "chapter_analysis"
    pacing_analysis = "pacing_analysis"


class IssueSeverity(str, enum.Enum):
    critical = "critical"
    warning = "warning"
    note = "note"


# --- MODELS ---


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verification_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verification_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    password_reset_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    password_reset_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_provisional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subscription_status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status", create_constraint=False, create_type=False),
        nullable=False,
        default=SubscriptionStatus.free,
    )
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    tos_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    manuscripts: Mapped[list["Manuscript"]] = relationship(back_populates="user")
    email_events: Mapped[list["EmailEvent"]] = relationship(back_populates="user")


class Manuscript(Base):
    __tablename__ = "manuscripts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    genre: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    word_count_est: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chapter_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[ManuscriptStatus] = mapped_column(
        Enum(ManuscriptStatus, name="manuscript_status", create_constraint=False, create_type=False),
        nullable=False,
        default=ManuscriptStatus.uploading,
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status", create_constraint=False, create_type=False),
        nullable=False,
        default=PaymentStatus.unpaid,
    )
    s3_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stripe_session_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    user: Mapped["User"] = relationship(back_populates="manuscripts")
    chapters: Mapped[list["Chapter"]] = relationship(back_populates="manuscript")
    story_bible: Mapped[Optional["StoryBible"]] = relationship(back_populates="manuscript", uselist=False)
    jobs: Mapped[list["Job"]] = relationship(back_populates="manuscript")


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("manuscript_id", "chapter_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manuscript_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("manuscripts.id"), nullable=False)
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    s3_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    word_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[ChapterStatus] = mapped_column(
        Enum(ChapterStatus, name="chapter_status", create_constraint=False, create_type=False),
        nullable=False,
        default=ChapterStatus.uploaded,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    manuscript: Mapped["Manuscript"] = relationship(back_populates="chapters")
    analyses: Mapped[list["ChapterAnalysis"]] = relationship(back_populates="chapter")


class StoryBible(Base):
    __tablename__ = "story_bibles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manuscript_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manuscripts.id"), unique=True, nullable=False
    )
    bible_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    manuscript: Mapped["Manuscript"] = relationship(back_populates="story_bible")
    versions: Mapped[list["StoryBibleVersion"]] = relationship(back_populates="story_bible")


class StoryBibleVersion(Base):
    __tablename__ = "story_bible_versions"
    __table_args__ = (UniqueConstraint("story_bible_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    story_bible_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("story_bibles.id"), nullable=False)
    bible_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_chapter_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    story_bible: Mapped["StoryBible"] = relationship(back_populates="versions")


class ChapterAnalysis(Base):
    __tablename__ = "chapter_analyses"
    __table_args__ = (UniqueConstraint("chapter_id", "prompt_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chapter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=False)
    issues_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    pacing_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    genre_notes: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    chapter: Mapped["Chapter"] = relationship(back_populates="analyses")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    manuscript_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("manuscripts.id"), nullable=False)
    chapter_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("chapters.id"), nullable=True)
    job_type: Mapped[JobType] = mapped_column(
        Enum(JobType, name="job_type", create_constraint=False, create_type=False), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status", create_constraint=False, create_type=False), nullable=False, default=JobStatus.pending
    )
    progress_pct: Mapped[int] = mapped_column(
        Integer, CheckConstraint("progress_pct >= 0 AND progress_pct <= 100"), nullable=False, default=0
    )
    current_step: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    manuscript: Mapped["Manuscript"] = relationship(back_populates="jobs")


class EmailEvent(Base):
    __tablename__ = "email_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    manuscript_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("manuscripts.id"), nullable=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default="now()")

    user: Mapped["User"] = relationship(back_populates="email_events")
