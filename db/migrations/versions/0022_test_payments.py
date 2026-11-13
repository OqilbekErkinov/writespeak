"""test payments

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-14

- payment_requests.is_test: a payment approved only to try the bot out (the
  user, 2026-11-14: of all approved payments only 5 000 so'm was real money)
  - it still gave its coins, but never counts as revenue: the dashboard, the
  daily report, /stats and the users' "paid" totals leave it out.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_requests",
                  sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("payment_requests", "is_test")
