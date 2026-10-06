"""practice question images + spoken prompt source

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06

- practice_questions.image_path: chart/diagram for Writing Task 1, uploaded
  from the web admin panel's question manager (webapp/main.py).
- 'audio' source type: prompts/questions can now be sent as voice/audio and
  are transcribed (bot/utils/input_extraction.py).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("practice_questions", sa.Column("image_path", sa.String(1024), nullable=True))
    # Allowed inside a transaction on Postgres 12+ (we run 16), as long as the
    # new value isn't used in the same transaction.
    op.execute("ALTER TYPE prompt_source_type ADD VALUE IF NOT EXISTS 'audio'")
    op.execute("ALTER TYPE answer_source_type ADD VALUE IF NOT EXISTS 'audio'")


def downgrade() -> None:
    op.drop_column("practice_questions", "image_path")
    # Postgres can't drop an enum value; the unused 'audio' label is harmless.
