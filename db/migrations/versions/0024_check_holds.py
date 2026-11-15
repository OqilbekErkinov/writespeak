"""check holds

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-15

- check_holds: one row per Writing/Speaking check while it is graded and
  how it ended (bot/utils/check_guard.py, the 2026-11-15 audit). The coins
  are taken when grading starts and given back unless the report is saved;
  the partial unique index lets a student have one running check at a time
  (admins' rows are not exclusive). A bot restart refunds what was still
  held and offers "try again".
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "check_holds",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("module", sa.String(1), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("lang", sa.String(5), nullable=False, server_default="uz"),
        sa.Column("payer_id", sa.BigInteger(), nullable=True),
        sa.Column("coins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exclusive", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("state", sa.String(12), nullable=False, server_default="held"),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("submission_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_check_holds_user_id", "check_holds", ["user_id"])
    op.create_index("ix_check_holds_created_at", "check_holds", ["created_at"])
    op.create_index("uq_check_holds_one_running", "check_holds", ["user_id"], unique=True,
                    postgresql_where=sa.text("state = 'held' AND exclusive"))


def downgrade() -> None:
    op.drop_index("uq_check_holds_one_running", table_name="check_holds")
    op.drop_index("ix_check_holds_created_at", table_name="check_holds")
    op.drop_index("ix_check_holds_user_id", table_name="check_holds")
    op.drop_table("check_holds")
