"""Initial schema per DECISION_001 with JUDGE amendments.

Revision ID: 001
Revises: None
Create Date: 2026-03-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- ENUM TYPES ---
    # Enums are created inline by sa.Enum(...) in the create_table calls below.
    # No explicit CREATE TYPE needed — SQLAlchemy handles it.

    # --- TABLES ---

    # 1. USERS
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.Text, unique=True, nullable=False),
        sa.Column("password_hash", sa.Text, nullable=True),
        sa.Column("email_verified", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("verification_token", sa.Text, nullable=True),
        sa.Column("verification_token_expires", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_reset_token", sa.Text, nullable=True),
        sa.Column("password_reset_token_expires", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_provisional", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("stripe_customer_id", sa.Text, nullable=True),
        sa.Column(
            "subscription_status",
            sa.Enum("free", "per_use", "subscribed", name="subscription_status"),
            nullable=False,
            server_default="free",
        ),
        sa.Column("tos_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # 2. MANUSCRIPTS
    op.create_table(
        "manuscripts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("genre", sa.Text, nullable=True),
        sa.Column("word_count_est", sa.Integer, nullable=True),
        sa.Column("chapter_count", sa.Integer, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "uploading", "extracting", "bible_generating", "bible_complete",
                "analyzing", "complete", "error",
                name="manuscript_status",
            ),
            nullable=False,
            server_default="uploading",
        ),
        sa.Column(
            "payment_status",
            sa.Enum("unpaid", "paid", "refunded", name="payment_status"),
            nullable=False,
            server_default="unpaid",
        ),
        sa.Column("s3_key", sa.Text, nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # 3. CHAPTERS
    op.create_table(
        "chapters",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("manuscript_id", UUID(as_uuid=True), sa.ForeignKey("manuscripts.id"), nullable=False),
        sa.Column("chapter_number", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("raw_text", sa.Text, nullable=True),
        sa.Column("s3_key", sa.Text, nullable=True),
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "uploaded", "extracting", "extracted", "analyzing", "analyzed", "error",
                name="chapter_status",
            ),
            nullable=False,
            server_default="uploaded",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("manuscript_id", "chapter_number"),
    )

    # 4. STORY BIBLES
    op.create_table(
        "story_bibles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("manuscript_id", UUID(as_uuid=True), sa.ForeignKey("manuscripts.id"), unique=True, nullable=False),
        sa.Column("bible_json", JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # 5. STORY BIBLE VERSIONS
    op.create_table(
        "story_bible_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("story_bible_id", UUID(as_uuid=True), sa.ForeignKey("story_bibles.id"), nullable=False),
        sa.Column("bible_json", JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_by_chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("story_bible_id", "version"),
    )

    # 6. CHAPTER ANALYSES (with JUDGE amendment: unique on chapter_id + prompt_version)
    op.create_table(
        "chapter_analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id"), nullable=False),
        sa.Column("issues_json", JSONB, nullable=False),
        sa.Column("pacing_json", JSONB, nullable=True),
        sa.Column("genre_notes", JSONB, nullable=True),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("chapter_id", "prompt_version"),
    )

    # 7. JOBS
    op.create_table(
        "jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("manuscript_id", UUID(as_uuid=True), sa.ForeignKey("manuscripts.id"), nullable=False),
        sa.Column("chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id"), nullable=True),
        sa.Column(
            "job_type",
            sa.Enum(
                "text_extraction", "story_bible_generation", "chapter_analysis", "pacing_analysis",
                name="job_type",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "running", "completed", "failed", "cancelled",
                name="job_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("progress_pct", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("current_step", sa.Text, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("attempts", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default=sa.text("3")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("progress_pct >= 0 AND progress_pct <= 100"),
    )

    # 8. EMAIL EVENTS
    op.create_table(
        "email_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("manuscript_id", UUID(as_uuid=True), sa.ForeignKey("manuscripts.id"), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # --- INDEXES ---
    op.create_index("idx_users_email_active", "users", ["email"], postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_index("idx_manuscripts_user_active", "manuscripts", ["user_id"], postgresql_where=sa.text("deleted_at IS NULL"))
    op.create_index("idx_users_pending_purge", "users", ["deleted_at"], postgresql_where=sa.text("deleted_at IS NOT NULL"))
    op.create_index("idx_manuscripts_pending_purge", "manuscripts", ["deleted_at"], postgresql_where=sa.text("deleted_at IS NOT NULL"))
    op.create_index("idx_chapters_manuscript", "chapters", ["manuscript_id"])
    op.create_index("idx_analyses_chapter", "chapter_analyses", ["chapter_id"])
    op.create_index("idx_jobs_pending", "jobs", ["status", "created_at"], postgresql_where=sa.text("status IN ('pending', 'running')"))
    op.create_index("idx_jobs_manuscript", "jobs", ["manuscript_id"])
    op.create_index("idx_email_events_unsent", "email_events", ["scheduled_at"], postgresql_where=sa.text("sent_at IS NULL"))
    op.create_index("idx_bible_versions_bible", "story_bible_versions", ["story_bible_id"])
    # JUDGE amendment: provisional user stale cleanup index
    op.create_index(
        "idx_users_provisional_stale", "users", ["created_at"],
        postgresql_where=sa.text("is_provisional = TRUE AND deleted_at IS NULL"),
    )

    # --- updated_at TRIGGER ---
    op.execute("""
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    for table in ["users", "manuscripts", "chapters", "story_bibles", "jobs"]:
        op.execute(f"""
            CREATE TRIGGER trg_{table}_updated_at
                BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """)


def downgrade() -> None:
    for table in ["jobs", "story_bibles", "chapters", "manuscripts", "users"]:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")

    op.drop_table("email_events")
    op.drop_table("jobs")
    op.drop_table("chapter_analyses")
    op.drop_table("story_bible_versions")
    op.drop_table("story_bibles")
    op.drop_table("chapters")
    op.drop_table("manuscripts")
    op.drop_table("users")

    for enum_name in [
        "issue_severity", "job_type", "job_status", "chapter_status",
        "payment_status", "manuscript_status", "subscription_status",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
