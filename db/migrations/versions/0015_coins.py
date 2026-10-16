"""coins instead of credits

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-17

The bot sells coins (bot/utils/coins.py) instead of "1 credit = 1 check at
5000 so'm"; every check type costs its own number of coins.

- users.credit_balance -> coin_balance, x5 (one credit was worth 5000 so'm,
  a coin is nominally 1000).
- Each user's unused one-off free checks (2 per user, counted from their
  checks; a full Speaking test counted 3) become 5 coins each - the 10-coin
  welcome gift that replaces them, for everyone who hadn't used them.
- payment_requests.credits -> coins, x5 for coin/credit purchases.
- writing/speaking_submissions.coins_spent: what a check cost; checks a
  group's sponsor paid for so far get 5 coins (a full test 15).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COINS_PER_CREDIT = 5
OLD_FREE_CHECKS = 2


def upgrade() -> None:
    op.alter_column("users", "credit_balance", new_column_name="coin_balance")
    op.alter_column("payment_requests", "credits", new_column_name="coins")
    for table in ("writing_submissions", "speaking_submissions"):
        op.add_column(table, sa.Column("coins_spent", sa.Integer(), nullable=False, server_default="0"))

    op.execute(f"UPDATE users SET coin_balance = coin_balance * {COINS_PER_CREDIT}")
    op.execute(f"UPDATE payment_requests SET coins = coins * {COINS_PER_CREDIT} WHERE kind = 'credits'")
    op.execute(
        f"""
        UPDATE users u SET coin_balance = u.coin_balance + {COINS_PER_CREDIT} * GREATEST(0, {OLD_FREE_CHECKS} - (
            (SELECT count(*) FROM writing_submissions w WHERE w.user_id = u.id)
            + (SELECT coalesce(sum(CASE WHEN s.part = 'full' THEN 3 ELSE 1 END), 0)
               FROM speaking_submissions s WHERE s.user_id = u.id)
        ))
        """
    )
    op.execute(f"UPDATE writing_submissions SET coins_spent = {COINS_PER_CREDIT} WHERE paid_by_id IS NOT NULL")
    op.execute(
        f"UPDATE speaking_submissions SET coins_spent = CASE WHEN part = 'full' THEN {3 * COINS_PER_CREDIT} "
        f"ELSE {COINS_PER_CREDIT} END WHERE paid_by_id IS NOT NULL"
    )


def downgrade() -> None:
    # Approximate: coins back to whole credits (the free-check gift can't be
    # told apart from bought coins any more).
    op.execute(f"UPDATE payment_requests SET coins = coins / {COINS_PER_CREDIT} WHERE kind = 'credits'")
    op.execute(f"UPDATE users SET coin_balance = coin_balance / {COINS_PER_CREDIT}")
    for table in ("writing_submissions", "speaking_submissions"):
        op.drop_column(table, "coins_spent")
    op.alter_column("payment_requests", "coins", new_column_name="credits")
    op.alter_column("users", "coin_balance", new_column_name="credit_balance")
