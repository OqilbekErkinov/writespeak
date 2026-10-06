"""SQLAlchemy ORM models — full schema (see plan section 2).

Tables are grouped by module even though some (practice_progress, book_chunks)
are only populated starting from later build phases; defining the whole
schema up front avoids repeated migrations churn while the shape is already
agreed in the plan.
"""
from __future__ import annotations

import enum
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 1536  # text-embedding-3-small


class Base(DeclarativeBase):
    pass


class UserStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    denied = "denied"


class UserRole(str, enum.Enum):
    student = "student"
    admin = "admin"


class SourceType(str, enum.Enum):
    text = "text"
    photo = "photo"
    pdf = "pdf"
    docx = "docx"
    audio = "audio"  # spoken prompt/question, transcribed (migration 0007)


class TaskType(str, enum.Enum):
    task1 = "task1"
    task2 = "task2"


class SpeakingPart(str, enum.Enum):
    part1 = "part1"
    part2 = "part2"
    part3 = "part3"


class PracticeModule(str, enum.Enum):
    writing_task1 = "writing_task1"
    writing_task2 = "writing_task2"
    speaking_part1 = "speaking_part1"
    speaking_part2 = "speaking_part2"
    speaking_part3 = "speaking_part3"


class BookTaskType(str, enum.Enum):
    writing_task1 = "writing_task1"
    writing_task2 = "writing_task2"
    speaking = "speaking"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class PaymentKind(str, enum.Enum):
    credits = "credits"
    subscription = "subscription"


class MockTestStatus(str, enum.Enum):
    in_progress = "in_progress"
    completed = "completed"


class MockTestPartType(str, enum.Enum):
    writing_task1 = "writing_task1"
    writing_task2 = "writing_task2"
    speaking_part1 = "speaking_part1"
    speaking_part2 = "speaking_part2"
    speaking_part3 = "speaking_part3"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # telegram user id
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status"), default=UserStatus.pending
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role"), default=UserRole.student
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Paid, never-expiring check credits bought beyond the free daily quota
    # (see bot/utils/quota.py). One credit = one Writing or Speaking check.
    credit_balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Unlimited-checks subscription (bot/utils/quota.py treats "now < this" as
    # unlimited, alongside the free quota and credit_balance).
    subscription_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Referral program (bot/handlers/referral.py): the referrer earns 1 credit
    # once this user completes their first check, tracked via
    # referral_rewarded so it only ever pays out once.
    referred_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    referral_rewarded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # UI language - "uz" or "ru" (see bot/i18n.py). Chosen once at first
    # /start (bot/handlers/start.py) and changeable from "Hisobim".
    language: Mapped[str] = mapped_column(String(5), default="uz", server_default="uz")

    writing_submissions: Mapped[list["WritingSubmission"]] = relationship(back_populates="user")
    speaking_submissions: Mapped[list["SpeakingSubmission"]] = relationship(back_populates="user")


class WritingSubmission(Base):
    __tablename__ = "writing_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType, name="writing_task_type"))

    prompt_text: Mapped[str] = mapped_column(Text)
    prompt_source: Mapped[SourceType] = mapped_column(Enum(SourceType, name="prompt_source_type"))
    answer_text: Mapped[str] = mapped_column(Text)
    answer_source: Mapped[SourceType] = mapped_column(Enum(SourceType, name="answer_source_type"))
    original_file_paths: Mapped[dict] = mapped_column(JSON, default=dict)

    overall_band: Mapped[float | None] = mapped_column(Float, nullable=True)
    criteria_scores: Mapped[dict] = mapped_column(JSON, default=dict)  # {TA, CC, LR, GRA}
    annotations: Mapped[list] = mapped_column(JSON, default=list)  # [{sentence, color, comment}]
    vocabulary_json: Mapped[list] = mapped_column(JSON, default=list)
    book_sample_ref: Mapped[int | None] = mapped_column(ForeignKey("book_chunks.id"), nullable=True)

    feedback_pdf_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    practice_question_id: Mapped[int | None] = mapped_column(
        ForeignKey("practice_questions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="writing_submissions")


class SpeakingSubmission(Base):
    __tablename__ = "speaking_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    part: Mapped[SpeakingPart] = mapped_column(Enum(SpeakingPart, name="speaking_part"))

    question_text: Mapped[str] = mapped_column(Text)
    audio_file_path: Mapped[str] = mapped_column(String(1024))
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    overall_band: Mapped[float | None] = mapped_column(Float, nullable=True)
    criteria_scores: Mapped[dict] = mapped_column(JSON, default=dict)  # {FC, LR, GRA, Pronunciation}
    annotations: Mapped[list] = mapped_column(JSON, default=list)
    vocabulary_json: Mapped[list] = mapped_column(JSON, default=list)
    book_sample_ref: Mapped[int | None] = mapped_column(ForeignKey("book_chunks.id"), nullable=True)

    feedback_pdf_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    practice_question_id: Mapped[int | None] = mapped_column(
        ForeignKey("practice_questions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="speaking_submissions")


class PracticeQuestion(Base):
    __tablename__ = "practice_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    module: Mapped[PracticeModule] = mapped_column(Enum(PracticeModule, name="practice_module"))
    # Speaking Part 1/3: one question per line, asked one at a time (see
    # bot/utils/speaking_questions.py). Part 2: the whole text is one cue card.
    question_text: Mapped[str] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    # Chart/diagram for Writing Task 1, uploaded from the web admin panel
    # (webapp/main.py) - shown to the student and passed to the grader.
    image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)


class PracticeProgress(Base):
    __tablename__ = "practice_progress"
    __table_args__ = (UniqueConstraint("user_id", "practice_question_id", name="uq_user_question"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    practice_question_id: Mapped[int] = mapped_column(ForeignKey("practice_questions.id"))
    submission_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submission_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # writing|speaking
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BookChunk(Base):
    __tablename__ = "book_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    book_title: Mapped[str] = mapped_column(String(255))
    author: Mapped[str] = mapped_column(String(255))
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_type: Mapped[BookTaskType] = mapped_column(Enum(BookTaskType, name="book_task_type"))
    topic_tags: Mapped[list] = mapped_column(JSON, default=list)
    band_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    content_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))


class PaymentRequest(Base):
    """One card-transfer top-up request: N credits for N * settings.price_per_check
    so'm, reviewed by an admin against the receipt screenshot the user sends
    (see bot/handlers/payments.py). Credits are only added to the user's
    balance once an admin approves it."""

    __tablename__ = "payment_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[PaymentKind] = mapped_column(
        Enum(PaymentKind, name="payment_kind"), default=PaymentKind.credits
    )
    credits: Mapped[int] = mapped_column(Integer)  # 0 for a subscription purchase
    subscription_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount: Mapped[int] = mapped_column(Integer)  # so'm
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.pending
    )
    receipt_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    user: Mapped["User"] = relationship()


class VocabularyEntry(Base):
    """One saved word/phrase from a graded submission's vocabulary list, for
    the 'Lug'atim' flashcard review flow (bot/handlers/vocabulary.py)."""

    __tablename__ = "vocabulary_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    word_or_phrase: Mapped[str] = mapped_column(String(255))
    meaning: Mapped[str] = mapped_column(Text)
    example_sentence: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))  # "writing" | "speaking"
    is_learned: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MockTestSession(Base):
    """A full simulated IELTS session chaining all 5 sub-tests (Writing
    Task 1+2, Speaking Part 1-3) back to back - see bot/handlers/mock_test.py."""

    __tablename__ = "mock_test_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[MockTestStatus] = mapped_column(
        Enum(MockTestStatus, name="mock_test_status"), default=MockTestStatus.in_progress
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MockTestPart(Base):
    __tablename__ = "mock_test_parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("mock_test_sessions.id"))
    part_type: Mapped[MockTestPartType] = mapped_column(
        Enum(MockTestPartType, name="mock_test_part_type")
    )
    order_index: Mapped[int] = mapped_column(Integer)
    submission_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submission_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    band: Mapped[float | None] = mapped_column(Float, nullable=True)


class Group(Base):
    """A teacher's class (B2B: language centers/teachers) - see
    bot/handlers/group.py. MVP is deliberately 1 group per teacher
    (`teacher_id` unique) to keep the UI simple; a teacher who needs more
    than one class today just runs a second bot account.
    """

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GroupMembership(Base):
    """A student joining via https://t.me/<bot>?start=group_<id> (same
    deep-link mechanism as referrals, see bot/handlers/start.py)."""

    __tablename__ = "group_memberships"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
