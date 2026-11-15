"""receipt unique id

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-15

- payment_requests.receipt_unique_id: Telegram's file_unique_id of the
  receipt photo - the same picture sent for another payment (a second pack
  bought with one transfer's screenshot) is flagged on the admins' card
  before anyone approves it (the 2026-11-15 audit).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("payment_requests", sa.Column("receipt_unique_id", sa.String(64), nullable=True))
    op.create_index("ix_payment_requests_receipt_unique_id", "payment_requests", ["receipt_unique_id"])


def downgrade() -> None:
    op.drop_index("ix_payment_requests_receipt_unique_id", table_name="payment_requests")
    op.drop_column("payment_requests", "receipt_unique_id")
