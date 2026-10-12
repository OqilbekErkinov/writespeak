"""practice question type (for the Practice list's filter)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-13

- practice_questions.kind: the Writing question type (bar chart, map,
  opinion essay, ...) - set by the importer, which sees the Task 1 chart;
  the wording alone often just says "the chart below".
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("practice_questions", sa.Column("kind", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("practice_questions", "kind")
