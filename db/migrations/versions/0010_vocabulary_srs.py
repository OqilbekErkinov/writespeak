"""vocabulary bank: spaced repetition, no duplicates, translations, reminders

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-13

- vocabulary_entries: word_key (normalised word, unique per user - the same
  word from a second report is no longer saved twice; existing duplicates
  are removed, keeping a learned copy over an unlearned one, then the
  oldest), translations ({"uz": ..., "ru": ...}), and the Leitner schedule
  (box / due_at / review_count / lapse_count / last_reviewed_at) - see
  db/crud.py's VOCAB_INTERVAL_DAYS.
  Words marked "learned" by the old one-tap flow come back once more, in a
  week (box 3), instead of staying learned for good.
- users: vocab_reminders / vocab_reminded_on for the daily review reminder
  (bot/utils/vocab_reminders.py).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vocabulary_entries", sa.Column("word_key", sa.String(255), nullable=True))
    op.add_column(
        "vocabulary_entries",
        sa.Column("translations", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "vocabulary_entries", sa.Column("box", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "vocabulary_entries",
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
    )
    op.add_column(
        "vocabulary_entries",
        sa.Column("review_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "vocabulary_entries",
        sa.Column("lapse_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "vocabulary_entries",
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Same normalisation as db.crud.vocab_key: collapse whitespace, lowercase,
    # strip surrounding punctuation.
    op.execute(
        r"""UPDATE vocabulary_entries SET word_key = left(
            btrim(lower(regexp_replace(word_or_phrase, '\s+', ' ', 'g')), ' .,;:!?"'''), 255)"""
    )
    op.execute("DELETE FROM vocabulary_entries WHERE word_key = ''")
    op.execute(
        """DELETE FROM vocabulary_entries v USING vocabulary_entries w
        WHERE v.user_id = w.user_id AND v.word_key = w.word_key
          AND ((w.is_learned AND NOT v.is_learned)
               OR (w.is_learned = v.is_learned AND w.id < v.id))"""
    )
    op.execute(
        """UPDATE vocabulary_entries
        SET box = 3, review_count = 1, is_learned = false, due_at = now() + interval '7 days'
        WHERE is_learned"""
    )
    op.execute("UPDATE vocabulary_entries SET due_at = created_at WHERE box = 0")

    op.alter_column("vocabulary_entries", "word_key", nullable=False)
    op.create_unique_constraint("uq_vocab_user_word", "vocabulary_entries", ["user_id", "word_key"])
    op.create_index("ix_vocab_user_due", "vocabulary_entries", ["user_id", "due_at"])

    op.add_column(
        "users", sa.Column("vocab_reminders", sa.Boolean(), nullable=False, server_default="true")
    )
    op.add_column("users", sa.Column("vocab_reminded_on", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "vocab_reminded_on")
    op.drop_column("users", "vocab_reminders")

    op.drop_index("ix_vocab_user_due", table_name="vocabulary_entries")
    op.drop_constraint("uq_vocab_user_word", "vocabulary_entries", type_="unique")
    for column in (
        "last_reviewed_at", "lapse_count", "review_count", "due_at", "box", "translations", "word_key"
    ):
        op.drop_column("vocabulary_entries", column)
