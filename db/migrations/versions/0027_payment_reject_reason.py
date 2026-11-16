"""payment reject reason

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-16

- payment_requests.reject_reason: why an admin rejected a payment (a key of bot/keyboards/payment_kb.py's
  REJECT_REASONS: "amount", "unreadable", "reused", "no_money", or "none" for "Sababsiz"). The student is told
  the reason with a button to send the receipt again; the panel's /payments page and the admins' group card show
  it. NULL for approvals and for the rejections made before this.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: Union[str, None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_requests", sa.Column("reject_reason", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("payment_requests", "reject_reason")
