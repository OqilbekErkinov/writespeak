"""chart keys: the checked data sheet of a Task 1 visual

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-28

- chart_keys: a Task 1 chart's data sheet as the bot read it once, then
  checked by an admin on the web panel (/charts); the same chart sent again
  is judged by the checked sheet (services/ai/chart_keys.py).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chart_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("source", sa.String(10), nullable=False, server_default="student"),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("image_path", sa.String(1024), nullable=False),
        sa.Column("fingerprint", sa.String(16), nullable=True),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("sheet", sa.JSON(), nullable=False),
        sa.Column("kind", sa.String(30), nullable=True),
        sa.Column("practice_question_id", sa.Integer(),
                  sa.ForeignKey("practice_questions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uses", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_submission_id", sa.Integer(), nullable=True),
        sa.Column("verified_by", sa.String(100), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chart_keys_status", "chart_keys", ["status"])
    op.create_index("ix_chart_keys_fingerprint", "chart_keys", ["fingerprint"])
    op.create_index("ix_chart_keys_practice_question_id", "chart_keys", ["practice_question_id"])


def downgrade() -> None:
    op.drop_index("ix_chart_keys_practice_question_id", table_name="chart_keys")
    op.drop_index("ix_chart_keys_fingerprint", table_name="chart_keys")
    op.drop_index("ix_chart_keys_status", table_name="chart_keys")
    op.drop_table("chart_keys")
