"""finance entries

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-14

- finance_entries: money that moves outside the bot, entered by hand on the
  panel's new finance page (the user, 2026-11-14: "one finance section for
  full control - every expense, every income"): server, domain, ads, a cash
  payment... The approved payments and the OpenAI spend are counted there
  by themselves.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "finance_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_by", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_finance_entries_day", "finance_entries", ["day"])


def downgrade() -> None:
    op.drop_index("ix_finance_entries_day", table_name="finance_entries")
    op.drop_table("finance_entries")
