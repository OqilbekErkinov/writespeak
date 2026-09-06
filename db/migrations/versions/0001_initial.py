"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-08-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIM = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    user_status = sa.Enum("pending", "approved", "denied", name="user_status")
    user_role = sa.Enum("student", "admin", name="user_role")
    source_type_prompt = sa.Enum("text", "photo", "pdf", "docx", name="prompt_source_type")
    source_type_answer = sa.Enum("text", "photo", "pdf", "docx", name="answer_source_type")
    writing_task_type = sa.Enum("task1", "task2", name="writing_task_type")
    speaking_part = sa.Enum("part1", "part2", "part3", name="speaking_part")
    practice_module = sa.Enum(
        "writing_task1", "writing_task2", "speaking_part1", "speaking_part2", "speaking_part3",
        name="practice_module",
    )
    book_task_type = sa.Enum("writing_task1", "writing_task2", "speaking", name="book_task_type")

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("username", sa.String(255), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("status", user_status, nullable=False, server_default="pending"),
        sa.Column("role", user_role, nullable=False, server_default="student"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "practice_questions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("module", practice_module, nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("topic", sa.String(255), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "book_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("book_title", sa.String(255), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("task_type", book_task_type, nullable=False),
        sa.Column("topic_tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("band_level", sa.Float(), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
    )
    op.execute(
        "CREATE INDEX book_chunks_embedding_idx ON book_chunks "
        "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    op.create_table(
        "writing_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("task_type", writing_task_type, nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("prompt_source", source_type_prompt, nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("answer_source", source_type_answer, nullable=False),
        sa.Column("original_file_paths", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("overall_band", sa.Float(), nullable=True),
        sa.Column("criteria_scores", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("annotations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("vocabulary_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("book_sample_ref", sa.Integer(), sa.ForeignKey("book_chunks.id"), nullable=True),
        sa.Column("feedback_pdf_path", sa.String(1024), nullable=True),
        sa.Column(
            "practice_question_id", sa.Integer(), sa.ForeignKey("practice_questions.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "speaking_submissions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("part", speaking_part, nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("audio_file_path", sa.String(1024), nullable=False),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("overall_band", sa.Float(), nullable=True),
        sa.Column("criteria_scores", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("annotations", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("vocabulary_json", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("book_sample_ref", sa.Integer(), sa.ForeignKey("book_chunks.id"), nullable=True),
        sa.Column("feedback_pdf_path", sa.String(1024), nullable=True),
        sa.Column(
            "practice_question_id", sa.Integer(), sa.ForeignKey("practice_questions.id"), nullable=True
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "practice_progress",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "practice_question_id", sa.Integer(), sa.ForeignKey("practice_questions.id"), nullable=False
        ),
        sa.Column("submission_id", sa.Integer(), nullable=True),
        sa.Column("submission_type", sa.String(20), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "practice_question_id", name="uq_user_question"),
    )


def downgrade() -> None:
    op.drop_table("practice_progress")
    op.drop_table("speaking_submissions")
    op.drop_table("writing_submissions")
    op.execute("DROP INDEX IF EXISTS book_chunks_embedding_idx")
    op.drop_table("book_chunks")
    op.drop_table("practice_questions")
    op.drop_table("users")

    for enum_name in (
        "book_task_type",
        "practice_module",
        "speaking_part",
        "writing_task_type",
        "answer_source_type",
        "prompt_source_type",
        "user_role",
        "user_status",
    ):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
