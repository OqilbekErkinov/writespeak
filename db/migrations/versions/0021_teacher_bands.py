"""teachers' bands on checks; the teacher role in the web panel

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-14

- writing_submissions.teacher_bands, speaking_submissions.teacher_bands: an
  IELTS teacher's own marks for the check, entered in the web panel
  ({"bands": {...}, "overall", "by", "at", "note"}) - the bot's bands are
  compared with them every week, and teacher-marked Speaking checks become
  the scoring runs' anchors (the user, 2026-11-14).
- web_admins.role: "admin" (as before) or "teacher" - a teacher sees only
  the checks page, their reports and recordings, and enters bands.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("writing_submissions", sa.Column("teacher_bands", sa.JSON(), nullable=True))
    op.add_column("speaking_submissions", sa.Column("teacher_bands", sa.JSON(), nullable=True))
    op.add_column("web_admins", sa.Column("role", sa.String(20), nullable=False, server_default="admin"))


def downgrade() -> None:
    op.drop_column("web_admins", "role")
    op.drop_column("speaking_submissions", "teacher_bands")
    op.drop_column("writing_submissions", "teacher_bands")
