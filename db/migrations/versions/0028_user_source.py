"""users.source: where each user came from

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-16

The owner's request before opening the bot to everyone (2026-11-16): which link brought each user.
- users.source: written once, with the user row, at the first /start (bot/handlers/start.py,
  bot/utils/signup_source.py): a panel-made `src_<name>` link's name, or "referral" / "gift" / "group" /
  "center" / "direct" / "other". Never changed afterwards.
- The users from before this keep NULL - the panel shows "noma'lum". Nothing was recorded then, and a made-up
  "unknown" written into every old row would look like data the bot had; NULL also keeps this migration a
  metadata-only change (no rewrite of the users table, no long lock while the bot runs).
- ix_users_source: the panel's per-source counts and the users list's source filter (/users?s=src:<name>).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: Union[str, None] = "0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("source", sa.String(64), nullable=True))
    op.create_index("ix_users_source", "users", ["source"])


def downgrade() -> None:
    op.drop_index("ix_users_source", table_name="users")
    op.drop_column("users", "source")
