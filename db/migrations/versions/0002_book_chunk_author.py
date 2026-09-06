"""add author column to book_chunks

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-10

Multiple books contain essays by more than one author on the same topic
(e.g. two instructors answering the same Task 2 prompt). `author` lets
rag_book_search.py cite the specific writer, and keeps chunking scoped to
one author's own essay per row so two authors' work is never blended into
a single "sample".
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "book_chunks",
        sa.Column("author", sa.String(255), nullable=False, server_default="Unknown"),
    )
    op.alter_column("book_chunks", "author", server_default=None)


def downgrade() -> None:
    op.drop_column("book_chunks", "author")
