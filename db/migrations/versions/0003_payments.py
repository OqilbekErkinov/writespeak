"""add credit_balance and payment_requests

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-28

Removes the admin-approval signup gate in favor of an open bot with a free
daily quota (2 checks/day) plus paid, never-expiring credits bought via a
card transfer that an admin confirms against the receipt screenshot. See
bot/utils/quota.py and bot/handlers/payments.py.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("credit_balance", sa.Integer(), nullable=False, server_default="0"),
    )

    payment_status = sa.Enum("pending", "approved", "rejected", name="payment_status")

    op.create_table(
        "payment_requests",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("credits", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", payment_status, nullable=False, server_default="pending"),
        sa.Column("receipt_file_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("payment_requests")
    sa.Enum(name="payment_status").drop(op.get_bind(), checkfirst=True)
    op.drop_column("users", "credit_balance")
