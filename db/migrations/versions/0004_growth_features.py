"""referral, subscriptions, vocabulary bank, mock tests

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-29

Adds the schema for four growth features: referral program (User.referred_by
/ referral_rewarded), subscriptions (User.subscription_expires_at +
PaymentRequest.kind/subscription_months), a personal vocabulary bank
(vocabulary_entries), and full Mock Test sessions (mock_test_sessions /
mock_test_parts).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Referral + subscription columns on users ---
    op.add_column("users", sa.Column("subscription_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("referred_by", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column(
        "users",
        sa.Column("referral_rewarded", sa.Boolean(), nullable=False, server_default="false"),
    )

    # --- PaymentRequest: subscription support alongside credits ---
    payment_kind = sa.Enum("credits", "subscription", name="payment_kind")
    payment_kind.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "payment_requests",
        sa.Column("kind", payment_kind, nullable=False, server_default="credits"),
    )
    op.add_column("payment_requests", sa.Column("subscription_months", sa.Integer(), nullable=True))

    # --- Vocabulary bank ---
    op.create_table(
        "vocabulary_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("word_or_phrase", sa.String(255), nullable=False),
        sa.Column("meaning", sa.Text(), nullable=False),
        sa.Column("example_sentence", sa.Text(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("is_learned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # --- Mock Test sessions ---
    mock_test_status = sa.Enum("in_progress", "completed", name="mock_test_status")
    mock_test_part_type = sa.Enum(
        "writing_task1", "writing_task2", "speaking_part1", "speaking_part2", "speaking_part3",
        name="mock_test_part_type",
    )
    op.create_table(
        "mock_test_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", mock_test_status, nullable=False, server_default="in_progress"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "mock_test_parts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("mock_test_sessions.id"), nullable=False),
        sa.Column("part_type", mock_test_part_type, nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=True),
        sa.Column("submission_type", sa.String(20), nullable=True),
        sa.Column("band", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("mock_test_parts")
    op.drop_table("mock_test_sessions")
    sa.Enum(name="mock_test_part_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="mock_test_status").drop(op.get_bind(), checkfirst=True)

    op.drop_table("vocabulary_entries")

    op.drop_column("payment_requests", "subscription_months")
    op.drop_column("payment_requests", "kind")
    sa.Enum(name="payment_kind").drop(op.get_bind(), checkfirst=True)

    op.drop_column("users", "referral_rewarded")
    op.drop_column("users", "referred_by")
    op.drop_column("users", "subscription_expires_at")
