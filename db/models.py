"""SQLAlchemy ORM models — full schema (see plan section 2).

Tables are grouped by module even though some (practice_progress, book_chunks)
are only populated starting from later build phases; defining the whole
schema up front avoids repeated migrations churn while the shape is already
agreed in the plan.
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
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
    teacher = "teacher"  # may open a group (bot/handlers/group.py); granted by an admin


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
    # Daily "words to review" reminder (bot/utils/vocab_reminders.py) - on by
    # default, toggled from the Lug'atim menu; vocab_reminded_on is the
    # Tashkent date it was last sent, so it goes out at most once a day.
    vocab_reminders: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    vocab_reminded_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    writing_submissions: Mapped[list["WritingSubmission"]] = relationship(
        back_populates="user", foreign_keys="WritingSubmission.user_id"
    )
    speaking_submissions: Mapped[list["SpeakingSubmission"]] = relationship(
        back_populates="user", foreign_keys="SpeakingSubmission.user_id"
    )


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
    # The group assignment part this answers (bot/handlers/group_assignments.py).
    assignment_part_id: Mapped[int | None] = mapped_column(
        ForeignKey("assignment_parts.id", ondelete="SET NULL"), nullable=True
    )
    # Who paid for a check sponsored by a group's teacher/center (else NULL).
    paid_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="writing_submissions", foreign_keys=[user_id])


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
    assignment_part_id: Mapped[int | None] = mapped_column(
        ForeignKey("assignment_parts.id", ondelete="SET NULL"), nullable=True
    )
    paid_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="speaking_submissions", foreign_keys=[user_id])


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
    # (webapp/main.py) or imported from a channel - shown to the student and
    # passed to the grader.
    image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Questions imported from Telegram channels (services/practice_import/):
    # `source` names the importer ("cdireport" / "speaking_assistant"; None =
    # written in the admin panel), `source_ref` the channel post it came from,
    # `source_hash` the channel text it was last synced from (so an admin's
    # edit isn't overwritten until the channel itself changes), `seen_at` when
    # the question was last reported from an exam, `times_seen` how often.
    source: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    source_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    times_seen: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # Writing questions only: finds the same question reported again.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)


class PracticeImportPost(Base):
    """A channel post the practice importer has already looked at, so it is
    read (and paid for) once - see services/practice_import/writing.py."""

    __tablename__ = "practice_import_posts"
    __table_args__ = (UniqueConstraint("channel", "post_id", name="uq_import_channel_post"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(64))
    post_id: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # done | skipped | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    """One word/phrase in a student's 'Lug'atim' word bank (bot/handlers/
    vocabulary.py) - from a graded submission's vocabulary list, or typed in
    by the student. Reviewed with Leitner-style spaced repetition: see
    db/crud.py's VOCAB_INTERVAL_DAYS for how box/due_at move."""

    __tablename__ = "vocabulary_entries"
    __table_args__ = (
        UniqueConstraint("user_id", "word_key", name="uq_vocab_user_word"),
        Index("ix_vocab_user_due", "user_id", "due_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    word_or_phrase: Mapped[str] = mapped_column(String(255))
    # db.crud.vocab_key(word_or_phrase) - keeps one copy of a word per student.
    word_key: Mapped[str] = mapped_column(String(255))
    meaning: Mapped[str] = mapped_column(Text)
    example_sentence: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20))  # "writing" | "speaking" | "manual"
    # {"uz": ..., "ru": ...}, filled lazily for the student's UI language
    # (services/ai/vocab_helper.py).
    translations: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    # 0 = not yet answered right since it was added (or since it was last
    # forgotten); VOCAB_MASTERED_BOX = mastered, which also sets is_learned.
    box: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Next review; None once mastered.
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, server_default=func.now()
    )
    review_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lapse_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class Center(Base):
    """A learning center (B2B): its owner - made a center owner by an admin in
    the web panel - invites teachers with a signed link, sees all of their
    groups, and pays for the center's sponsored checks and gifts from their
    own credit balance (bot/handlers/group.py)."""

    __tablename__ = "centers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CenterTeacher(Base):
    __tablename__ = "center_teachers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    center_id: Mapped[int] = mapped_column(ForeignKey("centers.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)  # one center per teacher
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Group(Base):
    """A teacher's class (B2B: language centers/teachers) - see
    bot/handlers/group.py. A teacher may run several; only users with role
    `teacher` (granted by an admin or a center) or admins can open one.
    `center_id` ties it to a learning center, whose owner then manages it too
    and pays for it.
    """

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    center_id: Mapped[int | None] = mapped_column(ForeignKey("centers.id"), nullable=True)
    # The teacher/center pays for students' checks, at most `daily_limit` per
    # student per day (None = no limit; assignments are never limited).
    sponsor_checks: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    leaderboard_on: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    digest_week: Mapped[str | None] = mapped_column(String(10), nullable=True)  # ISO week of the last digest
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GroupMembership(Base):
    """A student joining via the group's signed invite link
    (bot/utils/group_invite.py, handled in bot/handlers/start.py). The
    teacher sees only work submitted after `joined_at`."""

    __tablename__ = "group_memberships"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Assignment(Base):
    """Homework (one part) or a group mock test (five parts: Writing Task 1/2,
    Speaking Part 1-3) set by a group's teacher - see
    bot/handlers/group_assignments.py."""

    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # "homework" | "mock"
    title: Mapped[str] = mapped_column(String(255))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reminded: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssignmentPart(Base):
    __tablename__ = "assignment_parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    module: Mapped[str] = mapped_column(String(32))  # a PracticeModule value
    prompt_text: Mapped[str] = mapped_column(Text)
    image_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    practice_question_id: Mapped[int | None] = mapped_column(
        ForeignKey("practice_questions.id", ondelete="SET NULL"), nullable=True
    )


class SubmissionComment(Base):
    """A teacher's comment, and optionally their own band, on a student's work."""

    __tablename__ = "submission_comments"
    __table_args__ = (Index("ix_submission_comments_work", "kind", "submission_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(1))  # "w" | "s"
    submission_id: Mapped[int] = mapped_column(Integer)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text)
    band: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
