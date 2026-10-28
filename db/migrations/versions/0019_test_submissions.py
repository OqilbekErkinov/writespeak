"""test submissions

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-28

- writing_submissions.is_test: a test run (the academic lead's acceptance
  runs, scripts/live_check.py) - never a "previous essay" on the progress
  page, never a resubmission to give back.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("writing_submissions",
                  sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("writing_submissions", "is_test")
