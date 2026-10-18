"""coin gifts and admin coin adjustments

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-18

- payment_kind gets 'gift': a coin pack bought for someone else.
- coin_gifts: the one-time gift links an approved gift payment creates
  (bot/handlers/start.py claims them).
- coin_adjustments: coins an admin added or took away by hand in the web
  panel (webapp/main.py: /users/<id>/coins).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A new enum value can't be used in the transaction that adds it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE payment_kind ADD VALUE IF NOT EXISTS 'gift'")

    op.create_table(
        "coin_gifts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("coins", sa.Integer(), nullable=False),
        sa.Column("buyer_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("payment_request_id", sa.Integer(), sa.ForeignKey("payment_requests.id"), nullable=True),
        sa.Column("claimed_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("payment_request_id"),
    )
    op.create_index("ix_coin_gifts_code", "coin_gifts", ["code"], unique=True)

    op.create_table(
        "coin_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_coin_adjustments_user_id", "coin_adjustments", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_coin_adjustments_user_id", table_name="coin_adjustments")
    op.drop_table("coin_adjustments")
    op.drop_index("ix_coin_gifts_code", table_name="coin_gifts")
    op.drop_table("coin_gifts")
    # Postgres can't drop an enum value; 'gift' stays in payment_kind, unused.
