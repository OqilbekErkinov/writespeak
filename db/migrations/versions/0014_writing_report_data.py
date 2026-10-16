"""writing report data: where the model answer came from

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-16

- writing_submissions.report_data: what the report keeps besides the marks -
  the model answer's source (ai_generated / published_reference /
  human_reviewed), the band it was aimed at (provisional), the priorities it
  demonstrates, the writer whose style it follows, and a resubmission's
  original essay. No new table.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("writing_submissions", sa.Column("report_data", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("writing_submissions", "report_data")
