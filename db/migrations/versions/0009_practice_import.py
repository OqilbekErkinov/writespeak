"""practice questions imported from Telegram channels

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-12

- practice_questions: source / source_ref / source_hash / seen_at /
  times_seen / embedding, for questions imported by
  services/practice_import/ (Writing from @cdireport, Speaking from
  @IELTS_Speaking_Assistant_App).
- practice_import_posts: channel posts the importer has already read.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("practice_questions", sa.Column("source", sa.String(40), nullable=True))
    op.add_column("practice_questions", sa.Column("source_ref", sa.String(120), nullable=True))
    op.add_column("practice_questions", sa.Column("source_hash", sa.String(64), nullable=True))
    op.add_column("practice_questions", sa.Column("seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "practice_questions", sa.Column("times_seen", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("practice_questions", sa.Column("embedding", Vector(1536), nullable=True))
    op.create_index("ix_practice_questions_source", "practice_questions", ["source"])

    op.create_table(
        "practice_import_posts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("channel", sa.String(64), nullable=False),
        sa.Column("post_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("channel", "post_id", name="uq_import_channel_post"),
    )


def downgrade() -> None:
    op.drop_table("practice_import_posts")
    op.drop_index("ix_practice_questions_source", table_name="practice_questions")
    for column in ("embedding", "times_seen", "seen_at", "source_hash", "source_ref", "source"):
        op.drop_column("practice_questions", column)
