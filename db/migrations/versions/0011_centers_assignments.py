"""learning centers, several groups per teacher, assignments, sponsored checks, teacher comments

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-13

- centers / center_teachers: a learning center (owner = an admin-made center
  owner) with its own teachers; its groups' credits come from the owner.
- groups: a teacher may now run several groups (unique teacher_id dropped);
  center_id, "center/teacher pays for checks" with a per-student daily
  limit, a leaderboard switch, and the ISO week of the last weekly digest.
- assignments / assignment_parts: homework and group mock tests (a mock is
  one assignment with five parts); writing/speaking submissions point at the
  part they answer, and record who paid for a sponsored check (paid_by_id).
- submission_comments: a teacher's comment (and optional band) on a
  student's Writing/Speaking work.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "centers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "center_teachers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("center_id", sa.Integer(), sa.ForeignKey("centers.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.drop_constraint("groups_teacher_id_key", "groups", type_="unique")
    op.create_index("ix_groups_teacher_id", "groups", ["teacher_id"])
    op.add_column("groups", sa.Column("center_id", sa.Integer(), sa.ForeignKey("centers.id"), nullable=True))
    op.add_column("groups", sa.Column("sponsor_checks", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("groups", sa.Column("daily_limit", sa.Integer(), nullable=True))
    op.add_column("groups", sa.Column("leaderboard_on", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("groups", sa.Column("digest_week", sa.String(10), nullable=True))

    op.create_table(
        "assignments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("kind", sa.String(20), nullable=False),  # homework | mock
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("reminded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "assignment_parts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("assignment_id", sa.Integer(), sa.ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("module", sa.String(32), nullable=False),  # practice_module values
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("image_path", sa.String(1024), nullable=True),
        sa.Column("practice_question_id", sa.Integer(), sa.ForeignKey("practice_questions.id", ondelete="SET NULL"), nullable=True),
    )
    for table in ("writing_submissions", "speaking_submissions"):
        op.add_column(table, sa.Column(
            "assignment_part_id", sa.Integer(), sa.ForeignKey("assignment_parts.id", ondelete="SET NULL"), nullable=True,
        ))
        op.create_index(f"ix_{table}_assignment_part_id", table, ["assignment_part_id"])
        op.add_column(table, sa.Column("paid_by_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True))

    op.create_table(
        "submission_comments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(1), nullable=False),  # w | s
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("band", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_submission_comments_work", "submission_comments", ["kind", "submission_id"])


def downgrade() -> None:
    op.drop_index("ix_submission_comments_work", table_name="submission_comments")
    op.drop_table("submission_comments")
    for table in ("writing_submissions", "speaking_submissions"):
        op.drop_column(table, "paid_by_id")
        op.drop_index(f"ix_{table}_assignment_part_id", table_name=table)
        op.drop_column(table, "assignment_part_id")
    op.drop_table("assignment_parts")
    op.drop_table("assignments")
    for column in ("digest_week", "leaderboard_on", "daily_limit", "sponsor_checks", "center_id"):
        op.drop_column("groups", column)
    op.drop_index("ix_groups_teacher_id", table_name="groups")
    op.create_unique_constraint("groups_teacher_id_key", "groups", ["teacher_id"])
    op.drop_table("center_teachers")
    op.drop_table("centers")
