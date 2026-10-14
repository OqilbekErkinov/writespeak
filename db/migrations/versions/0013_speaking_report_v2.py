"""speaking report v2: full tests and the saved voice analysis

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-13

- speaking_part gets 'full': a full Speaking test (Part 1 + 2 + 3 graded as
  one report, counted as 3 checks).
- speaking_submissions.report_data: what the next report needs from this one
  (voice metrics for the progress page, the recordings' fingerprint and the
  per-question transcripts for resubmission detection, the session number,
  the model-answer log). No new table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE speaking_part ADD VALUE IF NOT EXISTS 'full'")
    op.add_column("speaking_submissions", sa.Column("report_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    # PostgreSQL can't drop an enum value; 'full' stays (unused).
    op.drop_column("speaking_submissions", "report_data")
