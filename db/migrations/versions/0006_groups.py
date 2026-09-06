"""add groups (B2B: teachers/language centers)

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-29

A teacher creates one group (1:1 with teacher_id), students join via a
https://t.me/<bot>?start=group_<id> deep link (bot/handlers/start.py), and
the teacher can view per-student stats and gift credits from their own
balance to students in their group (bot/handlers/group.py) - reuses the
existing credit system rather than adding new payment infrastructure.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "groups",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("teacher_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "group_memberships",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("groups.id"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("group_id", "user_id", name="uq_group_member"),
    )


def downgrade() -> None:
    op.drop_table("group_memberships")
    op.drop_table("groups")
