"""add users.language

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-29

Bilingual UI (uz/ru) - see bot/i18n.py and bot/handlers/start.py's language
picker.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("language", sa.String(5), nullable=False, server_default="uz"),
    )


def downgrade() -> None:
    op.drop_column("users", "language")
