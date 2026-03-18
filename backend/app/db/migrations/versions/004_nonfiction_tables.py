"""Add nonfiction support: document_type, nonfiction_format columns on manuscripts,
plus argument_maps, nonfiction_section_results, and nonfiction_document_summaries tables.

Per DECISION_008: nonfiction section detection and database schema.

NOTE: Hard-delete of manuscript rows is ONLY permitted via the GDPR purge flow.
All new tables use ON DELETE CASCADE from manuscripts/chapters intentionally —
when a manuscript is truly purged, all analysis data must go with it.

Revision ID: 004
Revises: 003
Create Date: 2026-03-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Create enum types ---
    document_type_enum = sa.Enum("fiction", "nonfiction", name="document_type")
    document_type_enum.create(op.get_bind(), checkfirst=True)

    nonfiction_format_enum = sa.Enum(
        "academic", "personal_essay", "journalism", "self_help", "business",
        name="nonfiction_format",
    )
    nonfiction_format_enum.create(op.get_bind(), checkfirst=True)

    section_detection_method_enum = sa.Enum(
        "header", "chunked", name="section_detection_method"
    )
    section_detection_method_enum.create(op.get_bind(), checkfirst=True)

    nonfiction_dimension_enum = sa.Enum(
        "argument", "evidence", "clarity", "structure", "tone",
        name="nonfiction_dimension",
    )
    nonfiction_dimension_enum.create(op.get_bind(), checkfirst=True)

    # --- Add columns to manuscripts ---
    op.add_column(
        "manuscripts",
        sa.Column(
            "document_type",
            sa.Enum("fiction", "nonfiction", name="document_type", create_type=False),
            nullable=False,
            server_default="fiction",
        ),
    )
    op.add_column(
        "manuscripts",
        sa.Column(
            "nonfiction_format",
            sa.Enum(
                "academic", "personal_essay", "journalism", "self_help", "business",
                name="nonfiction_format",
                create_type=False,
            ),
            nullable=True,
        ),
    )
    # CHECK: fiction manuscripts must have nonfiction_format IS NULL
    op.create_check_constraint(
        "ck_manuscripts_nonfiction_format_consistency",
        "manuscripts",
        "(document_type = 'fiction' AND nonfiction_format IS NULL) "
        "OR (document_type = 'nonfiction')",
    )

    # --- Create argument_maps table ---
    op.create_table(
        "argument_maps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("manuscript_id", UUID(as_uuid=True), sa.ForeignKey("manuscripts.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("argument_map_json", JSONB, nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # --- Create nonfiction_section_results table ---
    op.create_table(
        "nonfiction_section_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("chapter_id", UUID(as_uuid=True), sa.ForeignKey("chapters.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("section_results_json", JSONB, nullable=False),
        sa.Column(
            "dimension",
            sa.Enum("argument", "evidence", "clarity", "structure", "tone",
                    name="nonfiction_dimension", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "section_detection_method",
            sa.Enum("header", "chunked", name="section_detection_method", create_type=False),
            nullable=False,
        ),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("chapter_id", "dimension", "prompt_version"),
    )
    # Composite index for common query: all results of dimension X for chapter Y
    op.create_index(
        "ix_nonfiction_section_results_chapter_dimension",
        "nonfiction_section_results",
        ["chapter_id", "dimension"],
    )

    # --- Create nonfiction_document_summaries table ---
    op.create_table(
        "nonfiction_document_summaries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("manuscript_id", UUID(as_uuid=True), sa.ForeignKey("manuscripts.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("summary_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("nonfiction_document_summaries")
    op.drop_table("nonfiction_section_results")
    op.drop_table("argument_maps")

    op.drop_constraint("ck_manuscripts_nonfiction_format_consistency", "manuscripts", type_="check")
    op.drop_column("manuscripts", "nonfiction_format")
    op.drop_column("manuscripts", "document_type")

    sa.Enum(name="nonfiction_dimension").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="section_detection_method").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="nonfiction_format").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="document_type").drop(op.get_bind(), checkfirst=True)
