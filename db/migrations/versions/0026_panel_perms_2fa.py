"""panel permissions + two-step sign-in

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-16

The owner's choice (2026-11-16) for the admin panel:
- web_admins.perms: what each admin may do beyond looking (comma-separated keys of webapp/panel.py's PERMS);
  NULL = everything, as before - the logins made before this keep their access until the owner narrows it.
- panel_second_factor: the Telegram account each panel login's sign-in codes go to (the owner's .env login
  too), linked from the bot (bot/utils/second_factor.py), and the owner's one-time recovery codes (hashes).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("web_admins", sa.Column("perms", sa.String(200), nullable=True))
    op.create_table(
        "panel_second_factor",
        sa.Column("login", sa.String(50), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_name", sa.String(200), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("recovery_hashes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("panel_second_factor")
    op.drop_column("web_admins", "perms")
