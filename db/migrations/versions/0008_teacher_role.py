"""teacher role for groups

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-12

- user_role gets 'teacher': only teachers (granted by an admin from the
  Telegram request flow, bot/handlers/group.py) and admins can open a group.
  Users who already own a group become teachers.
- Removes memberships where a teacher had joined their own group by opening
  their own invite link (bot/handlers/start.py now prevents it).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A new enum value can't be used in the transaction that adds it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'teacher'")
    op.execute(
        "UPDATE users SET role = 'teacher' "
        "WHERE role = 'student' AND id IN (SELECT teacher_id FROM groups)"
    )
    op.execute(
        "DELETE FROM group_memberships m USING groups g "
        "WHERE m.group_id = g.id AND m.user_id = g.teacher_id"
    )


def downgrade() -> None:
    op.execute("UPDATE users SET role = 'student' WHERE role = 'teacher'")
    # Postgres can't drop an enum value; the unused 'teacher' label is harmless.
