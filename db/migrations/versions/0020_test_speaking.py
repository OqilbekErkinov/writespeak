"""test speaking submissions

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-29

- speaking_submissions.is_test: a test run (the academic lead's acceptance
  runs, scripts/live_check.py) - like writing_submissions.is_test: no coins,
  never the "previous session" on the progress page, never a resubmission to
  give back, no word bank entries.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("speaking_submissions",
                  sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("speaking_submissions", "is_test")
