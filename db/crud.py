"""Small, focused query helpers used by handlers/services.

Kept deliberately thin (no repository abstraction) — each function takes an
already-open AsyncSession so callers control the transaction boundary via
`db.database.get_session()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from db.models import (
    Group,
    GroupMembership,
    MockTestPart,
    MockTestPartType,
    MockTestSession,
    MockTestStatus,
    PaymentKind,
    PaymentRequest,
    PaymentStatus,
    PracticeImportPost,
    PracticeModule,
    PracticeProgress,
    PracticeQuestion,
    SpeakingSubmission,
    User,
    UserRole,
    UserStatus,
    VocabularyEntry,
    WritingSubmission,
)
from services.ai.essay_marking import count_words
from services.ai.schemas import SpeakingGradingResult, VocabularyItem

# --- Users / access control -------------------------------------------------


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def create_user(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    full_name: str,
    referred_by: int | None = None,
) -> User:
    """The bot is open to anyone - no admin approval gate - so every new
    signup is created active right away. `referred_by` is the referrer's
    Telegram id from a `/start ref_<id>` deep link (bot/handlers/referral.py);
    the referrer is only rewarded once this user completes their first check
    (see reward_referrer_if_eligible), not just for signing up."""
    user = User(
        id=user_id,
        username=username,
        full_name=full_name,
        status=UserStatus.approved,
        approved_at=datetime.now(timezone.utc),
        referred_by=referred_by if referred_by != user_id else None,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_or_create_admin(
    session: AsyncSession, user_id: int, username: str | None, full_name: str
) -> User:
    """Admins (from settings.admin_ids) are auto-approved on first contact so
    they never get stuck behind their own gatekeeper."""
    user = await get_user(session, user_id)
    if user is not None:
        return user
    user = User(
        id=user_id,
        username=username,
        full_name=full_name,
        status=UserStatus.approved,
        role=UserRole.admin,
        approved_at=datetime.now(timezone.utc),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def set_user_status(session: AsyncSession, user_id: int, status: UserStatus) -> User | None:
    user = await get_user(session, user_id)
    if user is None:
        return None
    user.status = status
    if status == UserStatus.approved:
        user.approved_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(user)
    return user


# --- Submissions (Writing / Speaking) ---------------------------------------


async def save_writing_submission(
    session: AsyncSession,
    *,
    user_id: int,
    task_type: str,
    prompt_text: str,
    prompt_source: str,
    answer_text: str,
    answer_source: str,
    original_file_paths: dict,
    overall_band: float,
    criteria_scores: dict[str, float],
    annotations: list[dict],
    vocabulary: list[VocabularyItem],
    book_sample_ref: int | None,
    feedback_pdf_path: str,
    practice_question_id: int | None = None,
    spend_credit: bool = False,
) -> WritingSubmission:
    """`annotations` holds the report's numbered mistakes (each with its
    `criterion`) since the 2026-10 report redesign - get_writing_history
    compares their per-criterion counts with the student's next essay.
    Older rows hold the previous sentence-annotation format."""
    submission = WritingSubmission(
        user_id=user_id,
        task_type=task_type,
        prompt_text=prompt_text,
        prompt_source=prompt_source,
        answer_text=answer_text,
        answer_source=answer_source,
        original_file_paths=original_file_paths,
        overall_band=overall_band,
        criteria_scores=criteria_scores,
        annotations=annotations,
        vocabulary_json=[v.model_dump() for v in vocabulary],
        book_sample_ref=book_sample_ref,
        feedback_pdf_path=feedback_pdf_path,
        practice_question_id=practice_question_id,
    )
    session.add(submission)
    if spend_credit:
        await _spend_one_credit(session, user_id)
    await session.commit()
    await session.refresh(submission)
    return submission


async def save_speaking_submission(
    session: AsyncSession,
    *,
    user_id: int,
    part: str,
    question_text: str,
    audio_file_path: str,
    transcript_text: str,
    result: SpeakingGradingResult,
    book_sample_ref: int | None,
    feedback_pdf_path: str,
    practice_question_id: int | None = None,
    spend_credit: bool = False,
) -> SpeakingSubmission:
    submission = SpeakingSubmission(
        user_id=user_id,
        part=part,
        question_text=question_text,
        audio_file_path=audio_file_path,
        transcript_text=transcript_text,
        overall_band=result.overall_band,
        criteria_scores={
            "FC": result.fluency_and_coherence,
            "LR": result.lexical_resource,
            "GRA": result.grammatical_range_and_accuracy,
            "Pronunciation": result.pronunciation,
        },
        annotations=[a.model_dump() for a in result.annotations],
        vocabulary_json=[v.model_dump() for v in result.vocabulary],
        book_sample_ref=book_sample_ref,
        feedback_pdf_path=feedback_pdf_path,
        practice_question_id=practice_question_id,
    )
    session.add(submission)
    if spend_credit:
        await _spend_one_credit(session, user_id)
    await session.commit()
    await session.refresh(submission)
    return submission


async def get_writing_history(
    session: AsyncSession, user_id: int, task_type: str
) -> tuple[int, dict | None]:
    """(number of the student's earlier Writing submissions, their latest
    graded one of the same task type as {"band", "criteria_scores",
    "mistake_counts", "words"}) for the report's essay number and progress
    page. mistake_counts is None for reports made before the 2026-10
    redesign, whose annotations carry no criterion; words comes from the
    stored answer text."""
    count = (
        await session.execute(
            select(func.count()).select_from(WritingSubmission).where(WritingSubmission.user_id == user_id)
        )
    ).scalar() or 0
    latest = (
        await session.execute(
            select(WritingSubmission)
            .where(
                WritingSubmission.user_id == user_id,
                WritingSubmission.task_type == task_type,
                WritingSubmission.overall_band.is_not(None),
            )
            .order_by(WritingSubmission.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        return count, None
    items = latest.annotations or []
    mistake_counts = None
    # Old-format rows always annotate every sentence, so an empty list can
    # only be a new-format report with no mistakes.
    if all(isinstance(i, dict) and "criterion" in i for i in items):
        mistake_counts = {}
        for item in items:
            mistake_counts[item["criterion"]] = mistake_counts.get(item["criterion"], 0) + 1
    return count, {
        "band": latest.overall_band,
        "criteria_scores": latest.criteria_scores or {},
        "mistake_counts": mistake_counts,
        "words": count_words(latest.answer_text) if latest.answer_text else None,
    }


async def _spend_one_credit(session: AsyncSession, user_id: int) -> None:
    user = await session.get(User, user_id)
    if user is not None and user.credit_balance > 0:
        user.credit_balance -= 1


async def get_writing_submission(
    session: AsyncSession, submission_id: int, user_id: int
) -> WritingSubmission | None:
    stmt = select(WritingSubmission).where(
        WritingSubmission.id == submission_id, WritingSubmission.user_id == user_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_speaking_submission(
    session: AsyncSession, submission_id: int, user_id: int
) -> SpeakingSubmission | None:
    stmt = select(SpeakingSubmission).where(
        SpeakingSubmission.id == submission_id, SpeakingSubmission.user_id == user_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_total_submission_count(session: AsyncSession, user_id: int) -> int:
    """Lifetime count of delivered checks - the free-check quota
    (bot/utils/quota.py) and the referral reward's "first completed check"
    (see reward_referrer_if_eligible) both go by it."""
    w = await session.execute(
        select(func.count()).select_from(WritingSubmission).where(WritingSubmission.user_id == user_id)
    )
    s = await session.execute(
        select(func.count()).select_from(SpeakingSubmission).where(SpeakingSubmission.user_id == user_id)
    )
    return (w.scalar() or 0) + (s.scalar() or 0)


async def has_active_subscription(session: AsyncSession, user_id: int) -> bool:
    user = await session.get(User, user_id)
    return (
        user is not None
        and user.subscription_expires_at is not None
        and user.subscription_expires_at > datetime.now(timezone.utc)
    )


CRITERIA_LABELS = {
    "TA": "Task Achievement",
    "CC": "Coherence & Cohesion",
    "LR": "Lexical Resource",
    "GRA": "Grammar (Grammatical Range & Accuracy)",
    "FC": "Fluency & Coherence",
    "Pronunciation": "Pronunciation",
}


CRITERIA_TIPS = {
    "TA": "Savolning barcha qismiga to'liq javob bering va asosiy fikringizni "
    "kirish qismidayoq aniq bildiring.",
    "CC": "Paragraflar va fikrlar orasida bog'lovchi so'zlardan (however, therefore, "
    "in addition) foydalaning, har bir paragrafda bitta asosiy fikr bo'lsin.",
    "LR": "\"📚 Lug'atim\" bo'limidagi so'zlarni keyingi javoblaringizda faol "
    "ishlating, oddiy so'zlarni sinonimlar bilan almashtirishga harakat qiling.",
    "GRA": "Turli gap tuzilishlaridan (murakkab qo'shma gaplar, shart maylidagi "
    "gaplar) foydalaning - Practice bo'limida ko'proq mashq qilish yordam beradi.",
    "FC": "Javob berishda tabiiy pauza qiling va gapni oxirigacha tugatishga "
    "harakat qiling, fikrni yarim yo'lda tashlab ketmang.",
    "Pronunciation": "So'zlarni aniqroq talaffuz qiling va gap urg'usiga e'tibor "
    "bering - sekinroq gapirib mashq qilish talaffuzni yaxshilaydi.",
}


async def get_criteria_averages(
    session: AsyncSession, user_id: int, limit: int | None = None
) -> dict[str, float]:
    """Average of each scored criterion (keys from db.crud.CRITERIA_LABELS),
    across the user's most recent `limit` graded submissions (Writing+
    Speaking combined by date), or all of them if `limit` is None. LR and
    GRA are scored in both Writing and Speaking and are merged into one
    combined average per key - a simple, approximate 'where are you weakest'
    signal, not a strict per-skill breakdown."""
    w_rows = (
        await session.execute(
            select(WritingSubmission.created_at, WritingSubmission.criteria_scores).where(
                WritingSubmission.user_id == user_id, WritingSubmission.overall_band.is_not(None)
            )
        )
    ).all()
    s_rows = (
        await session.execute(
            select(SpeakingSubmission.created_at, SpeakingSubmission.criteria_scores).where(
                SpeakingSubmission.user_id == user_id, SpeakingSubmission.overall_band.is_not(None)
            )
        )
    ).all()

    all_rows = sorted([*w_rows, *s_rows], key=lambda r: r[0], reverse=True)
    if limit is not None:
        all_rows = all_rows[:limit]

    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for _created_at, scores in all_rows:
        for key, value in scores.items():
            if value is None:
                continue
            sums[key] = sums.get(key, 0.0) + value
            counts[key] = counts.get(key, 0) + 1
    return {key: sums[key] / counts[key] for key in sums}


# --- Referral program ---------------------------------------------------------


async def reward_referrer_if_eligible(session: AsyncSession, user_id: int) -> int | None:
    """Call after a submission is saved. Pays out 1 credit to the referrer
    the FIRST time the referred user completes a check (not just on signup,
    to cut down on throwaway-account abuse). Returns the referrer's id if a
    reward was just given, else None."""
    user = await session.get(User, user_id)
    if user is None or user.referred_by is None or user.referral_rewarded:
        return None
    if await get_total_submission_count(session, user_id) != 1:
        return None
    referrer = await session.get(User, user.referred_by)
    if referrer is None:
        return None
    referrer.credit_balance += 1
    user.referral_rewarded = True
    await session.commit()
    return referrer.id


async def get_referral_count(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(User).where(User.referred_by == user_id)
    )
    return result.scalar() or 0


# --- Vocabulary bank -----------------------------------------------------------
# Leitner-style spaced repetition (bot/handlers/vocabulary.py): a word starts
# in box 0 and is due at once. Each right answer moves it one box up and
# schedules the next review VOCAB_INTERVAL_DAYS[box] days later (from Tashkent
# midnight, so "tomorrow" means the whole of tomorrow); reaching
# VOCAB_MASTERED_BOX marks it learned and takes it out of the queue. A wrong
# answer sends it back to box 0, due again at once.

VOCAB_INTERVAL_DAYS = {1: 1, 2: 3, 3: 7, 4: 14}
VOCAB_MASTERED_BOX = 5
# Words a student may add by hand per Tashkent day - each one is an AI call.
VOCAB_MANUAL_DAILY_LIMIT = 30

_VOCAB_KEY_STRIP = " .,;:!?\"'"


def vocab_key(word: str) -> str:
    """The form used to spot the same word twice for one student. Must match
    the backfill SQL in migration 0010."""
    return " ".join(word.split()).lower().strip(_VOCAB_KEY_STRIP)[:255]


def _tashkent_midnight_utc(days_ahead: int) -> datetime:
    return _today_start_tashkent_as_utc() + timedelta(days=days_ahead)


@dataclass
class VocabularyStats:
    total: int
    new: int  # never reviewed
    learning: int
    mastered: int
    due: int  # due now
    next_due_at: datetime | None  # earliest review among the not-mastered words


async def _insert_vocabulary(session: AsyncSession, user_id: int, rows: list[dict]) -> set[str]:
    """Inserts the rows the student doesn't have yet and returns their
    word_keys."""
    now = datetime.now(timezone.utc)
    values, seen = [], set()
    for row in rows:
        word = " ".join(row["word"].split())[:255]
        key = vocab_key(word)
        if not key or key in seen:
            continue
        seen.add(key)
        values.append(
            {
                "user_id": user_id,
                "word_or_phrase": word,
                "word_key": key,
                "meaning": row["meaning"].strip(),
                "example_sentence": row["example"].strip(),
                "source": row["source"],
                "translations": row.get("translations") or {},
                "due_at": now,
            }
        )
    if not values:
        return set()
    stmt = (
        pg_insert(VocabularyEntry)
        .values(values)
        .on_conflict_do_nothing(index_elements=["user_id", "word_key"])
        .returning(VocabularyEntry.word_key)
    )
    added = set((await session.execute(stmt)).scalars().all())
    await session.commit()
    return added


async def add_vocabulary_entries(
    session: AsyncSession, user_id: int, items: list[VocabularyItem], source: str
) -> int:
    """Saves a report's words, skipping ones the student already has; returns
    how many were new."""
    rows = [
        {"word": i.word_or_phrase, "meaning": i.meaning, "example": i.example_sentence, "source": source}
        for i in items
    ]
    return len(await _insert_vocabulary(session, user_id, rows))


async def add_manual_vocabulary(session: AsyncSession, user_id: int, rows: list[dict]) -> set[str]:
    """Words the student typed in (rows: word/meaning/example/translations);
    returns the word_keys actually added."""
    return await _insert_vocabulary(session, user_id, [{**r, "source": "manual"} for r in rows])


async def count_manual_vocabulary_today(session: AsyncSession, user_id: int) -> int:
    result = await session.execute(
        select(func.count())
        .select_from(VocabularyEntry)
        .where(
            VocabularyEntry.user_id == user_id,
            VocabularyEntry.source == "manual",
            VocabularyEntry.created_at >= _today_start_tashkent_as_utc(),
        )
    )
    return result.scalar() or 0


async def get_vocabulary_stats(session: AsyncSession, user_id: int) -> VocabularyStats:
    now = datetime.now(timezone.utc)
    learning = VocabularyEntry.is_learned.is_(False)
    row = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(VocabularyEntry.is_learned.is_(True)),
                func.count().filter(learning, VocabularyEntry.review_count == 0),
                func.count().filter(learning, VocabularyEntry.due_at <= now),
                func.min(VocabularyEntry.due_at).filter(learning),
            ).where(VocabularyEntry.user_id == user_id)
        )
    ).one()
    total, mastered, new, due, next_due_at = row
    return VocabularyStats(
        total=total,
        new=new,
        learning=total - mastered - new,
        mastered=mastered,
        due=due,
        next_due_at=next_due_at,
    )


async def get_vocabulary_counts(session: AsyncSession, user_id: int) -> tuple[int, int]:
    """Returns (total, mastered)."""
    stats = await get_vocabulary_stats(session, user_id)
    return stats.total, stats.mastered


async def get_vocabulary_due(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[VocabularyEntry]:
    """Words due for review: ones already being learned first (oldest due
    first), then new ones."""
    now = datetime.now(timezone.utc)
    stmt = (
        select(VocabularyEntry)
        .where(
            VocabularyEntry.user_id == user_id,
            VocabularyEntry.is_learned.is_(False),
            VocabularyEntry.due_at <= now,
        )
        .order_by(
            (VocabularyEntry.review_count == 0).asc(),
            VocabularyEntry.due_at,
            VocabularyEntry.id,
        )
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_vocabulary_for_test(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[VocabularyEntry]:
    """Test questions: due words first, then other words still being
    learned, then mastered ones - each group in random order."""
    now = datetime.now(timezone.utc)
    learning = VocabularyEntry.is_learned.is_(False)
    rank = case(
        (and_(learning, VocabularyEntry.due_at <= now), 0),
        (learning, 1),
        else_=2,
    )
    stmt = (
        select(VocabularyEntry)
        .where(VocabularyEntry.user_id == user_id)
        .order_by(rank, func.random())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_random_vocabulary(
    session: AsyncSession, user_id: int, exclude_ids: list[int], limit: int
) -> list[VocabularyEntry]:
    """Wrong options for test questions."""
    stmt = (
        select(VocabularyEntry)
        .where(VocabularyEntry.user_id == user_id, VocabularyEntry.id.not_in(exclude_ids or [0]))
        .order_by(func.random())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_vocabulary(
    session: AsyncSession, user_id: int, *, offset: int, limit: int
) -> tuple[list[VocabularyEntry], int]:
    """A page of the student's words, newest first, and the total count."""
    total = (
        await session.execute(
            select(func.count()).select_from(VocabularyEntry).where(VocabularyEntry.user_id == user_id)
        )
    ).scalar() or 0
    stmt = (
        select(VocabularyEntry)
        .where(VocabularyEntry.user_id == user_id)
        .order_by(VocabularyEntry.created_at.desc(), VocabularyEntry.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all()), total


async def get_vocabulary_entry(
    session: AsyncSession, user_id: int, entry_id: int
) -> VocabularyEntry | None:
    entry = await session.get(VocabularyEntry, entry_id)
    return entry if entry is not None and entry.user_id == user_id else None


async def delete_vocabulary_entry(session: AsyncSession, user_id: int, entry_id: int) -> bool:
    result = await session.execute(
        delete(VocabularyEntry).where(VocabularyEntry.id == entry_id, VocabularyEntry.user_id == user_id)
    )
    await session.commit()
    return result.rowcount > 0


async def reset_vocabulary_entry(session: AsyncSession, user_id: int, entry_id: int) -> bool:
    """'Learn it again': back to box 0, due at once."""
    entry = await get_vocabulary_entry(session, user_id, entry_id)
    if entry is None:
        return False
    entry.box = 0
    entry.is_learned = False
    entry.due_at = datetime.now(timezone.utc)
    await session.commit()
    return True


async def record_vocabulary_review(
    session: AsyncSession, user_id: int, entry_id: int, correct: bool, *, only_if_due: bool = False
) -> VocabularyEntry | None:
    """Moves a word along the Leitner schedule after one answer. With
    `only_if_due` (the test, which also asks words that aren't due), a right
    answer to a word that isn't due yet leaves its schedule alone - practising
    ahead doesn't skip a step - while a wrong one still sends it back."""
    entry = await get_vocabulary_entry(session, user_id, entry_id)
    if entry is None:
        return None
    now = datetime.now(timezone.utc)
    entry.review_count += 1
    entry.last_reviewed_at = now
    if not correct:
        entry.box = 0
        entry.lapse_count += 1
        entry.is_learned = False
        entry.due_at = now
    elif not (only_if_due and (entry.is_learned or (entry.due_at is not None and entry.due_at > now))):
        entry.box = min(entry.box + 1, VOCAB_MASTERED_BOX)
        if entry.box >= VOCAB_MASTERED_BOX:
            entry.is_learned = True
            entry.due_at = None
        else:
            entry.due_at = _tashkent_midnight_utc(VOCAB_INTERVAL_DAYS[entry.box])
    await session.commit()
    return entry


async def save_vocabulary_translations(
    session: AsyncSession, user_id: int, lang: str, translations: dict[int, str]
) -> None:
    """Stores {entry id: translation} for one UI language."""
    if not translations:
        return
    entries = (
        await session.execute(
            select(VocabularyEntry).where(
                VocabularyEntry.user_id == user_id, VocabularyEntry.id.in_(list(translations))
            )
        )
    ).scalars().all()
    for entry in entries:
        # A new dict, so SQLAlchemy sees the JSON column change.
        entry.translations = {**(entry.translations or {}), lang: translations[entry.id]}
    await session.commit()


async def set_vocab_reminders(session: AsyncSession, user_id: int, enabled: bool) -> None:
    await session.execute(update(User).where(User.id == user_id).values(vocab_reminders=enabled))
    await session.commit()


async def get_vocab_reminder_targets(
    session: AsyncSession, *, min_due: int, active_days: int
) -> list[tuple[int, str, int]]:
    """(user id, language, words due) for everyone to remind today: reminders
    on, not reminded yet today, at least `min_due` words due, no review yet
    today - and either reviewed within `active_days` or never reminded
    before (so someone who ignores the feature gets one nudge, not one a day)."""
    now = datetime.now(timezone.utc)
    today_start = _today_start_tashkent_as_utc()
    today = (now + _TASHKENT_OFFSET).date()
    reviewed = aliased(VocabularyEntry)
    reviewed_today = (
        select(reviewed.id)
        .where(reviewed.user_id == User.id, reviewed.last_reviewed_at >= today_start)
        .exists()
    )
    reviewed_lately = (
        select(reviewed.id)
        .where(
            reviewed.user_id == User.id,
            reviewed.last_reviewed_at >= now - timedelta(days=active_days),
        )
        .exists()
    )
    due = func.count(VocabularyEntry.id)
    stmt = (
        select(User.id, User.language, due)
        .join(VocabularyEntry, VocabularyEntry.user_id == User.id)
        .where(
            User.vocab_reminders.is_(True),
            User.status == UserStatus.approved,
            or_(User.vocab_reminded_on.is_(None), User.vocab_reminded_on < today),
            VocabularyEntry.is_learned.is_(False),
            VocabularyEntry.due_at <= now,
            ~reviewed_today,
            or_(reviewed_lately, User.vocab_reminded_on.is_(None)),
        )
        .group_by(User.id, User.language)
        .having(due >= min_due)
    )
    return [tuple(row) for row in (await session.execute(stmt)).all()]


async def mark_vocab_reminded(session: AsyncSession, user_ids: list[int], day: date) -> None:
    if user_ids:
        await session.execute(update(User).where(User.id.in_(user_ids)).values(vocab_reminded_on=day))
        await session.commit()


# --- Mock Test -----------------------------------------------------------------

_MOCK_TEST_PART_ORDER = [
    MockTestPartType.writing_task1,
    MockTestPartType.writing_task2,
    MockTestPartType.speaking_part1,
    MockTestPartType.speaking_part2,
    MockTestPartType.speaking_part3,
]


async def create_mock_test_session(session: AsyncSession, user_id: int) -> MockTestSession:
    mock = MockTestSession(user_id=user_id)
    session.add(mock)
    await session.flush()  # need mock.id for the parts below
    session.add_all(
        MockTestPart(session_id=mock.id, part_type=part_type, order_index=i)
        for i, part_type in enumerate(_MOCK_TEST_PART_ORDER)
    )
    await session.commit()
    await session.refresh(mock)
    return mock


async def get_mock_test_session(session: AsyncSession, session_id: int) -> MockTestSession | None:
    return await session.get(MockTestSession, session_id)


async def get_mock_test_parts(session: AsyncSession, session_id: int) -> list[MockTestPart]:
    stmt = (
        select(MockTestPart)
        .where(MockTestPart.session_id == session_id)
        .order_by(MockTestPart.order_index)
    )
    return list((await session.execute(stmt)).scalars().all())


async def complete_mock_test_part(
    session: AsyncSession,
    session_id: int,
    part_type: MockTestPartType,
    submission_id: int,
    submission_type: str,
    band: float | None,
) -> None:
    stmt = select(MockTestPart).where(
        MockTestPart.session_id == session_id, MockTestPart.part_type == part_type
    )
    part = (await session.execute(stmt)).scalar_one_or_none()
    if part is None:
        return
    part.submission_id = submission_id
    part.submission_type = submission_type
    part.band = band
    await session.commit()


async def finish_mock_test_session(session: AsyncSession, session_id: int) -> MockTestSession | None:
    mock = await session.get(MockTestSession, session_id)
    if mock is None:
        return None
    mock.status = MockTestStatus.completed
    mock.completed_at = datetime.now(timezone.utc)
    await session.commit()
    return mock


# --- Practice ----------------------------------------------------------------


async def get_practice_questions(
    session: AsyncSession, module: PracticeModule
) -> list[PracticeQuestion]:
    stmt = (
        select(PracticeQuestion)
        .where(PracticeQuestion.module == module)
        .order_by(PracticeQuestion.order_index, PracticeQuestion.id)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_practice_question(session: AsyncSession, question_id: int) -> PracticeQuestion | None:
    return await session.get(PracticeQuestion, question_id)


async def get_next_practice_question(
    session: AsyncSession, current: PracticeQuestion
) -> PracticeQuestion | None:
    """The question after `current` in its module's list order, if any."""
    stmt = (
        select(PracticeQuestion)
        .where(
            PracticeQuestion.module == current.module,
            or_(
                PracticeQuestion.order_index > current.order_index,
                (PracticeQuestion.order_index == current.order_index)
                & (PracticeQuestion.id > current.id),
            ),
        )
        .order_by(PracticeQuestion.order_index, PracticeQuestion.id)
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


# Practice question management (web admin panel, webapp/main.py) ------------


async def get_practice_question_counts(session: AsyncSession) -> dict[PracticeModule, int]:
    stmt = select(PracticeQuestion.module, func.count()).group_by(PracticeQuestion.module)
    counts = dict((await session.execute(stmt)).all())
    return {module: counts.get(module, 0) for module in PracticeModule}


async def create_practice_question(
    session: AsyncSession,
    *,
    module: PracticeModule,
    question_text: str,
    topic: str | None,
    image_path: str | None = None,
) -> PracticeQuestion:
    """Appended at the end of its module's list."""
    max_index = (
        await session.execute(
            select(func.max(PracticeQuestion.order_index)).where(PracticeQuestion.module == module)
        )
    ).scalar()
    question = PracticeQuestion(
        module=module,
        question_text=question_text,
        topic=topic,
        image_path=image_path,
        order_index=(max_index + 1) if max_index is not None else 0,
    )
    session.add(question)
    await session.commit()
    return question


async def update_practice_question(
    session: AsyncSession,
    question_id: int,
    *,
    module: PracticeModule,
    question_text: str,
    topic: str | None,
    image_path: str | None,
) -> PracticeQuestion | None:
    """Overwrites the content. Students who already completed it keep their
    ✅ only if the text and module stay the same - a rewritten question is a
    new one. Moving it to another module appends it at that module's end."""
    question = await session.get(PracticeQuestion, question_id)
    if question is None:
        return None
    if question.question_text != question_text or question.module != module:
        await session.execute(
            delete(PracticeProgress).where(PracticeProgress.practice_question_id == question_id)
        )
    if question.module != module:
        max_index = (
            await session.execute(
                select(func.max(PracticeQuestion.order_index)).where(PracticeQuestion.module == module)
            )
        ).scalar()
        question.module = module
        question.order_index = (max_index + 1) if max_index is not None else 0
    question.question_text = question_text
    question.topic = topic
    question.image_path = image_path
    await session.commit()
    return question


async def delete_practice_question(session: AsyncSession, question_id: int) -> PracticeQuestion | None:
    """Deletes the question and its completion marks; past submissions made
    from it stay in the students' history, just unlinked from the bank.
    Returns the deleted row (e.g. so the caller can remove its image)."""
    question = await session.get(PracticeQuestion, question_id)
    if question is None:
        return None
    await session.execute(
        delete(PracticeProgress).where(PracticeProgress.practice_question_id == question_id)
    )
    for model in (WritingSubmission, SpeakingSubmission):
        await session.execute(
            update(model).where(model.practice_question_id == question_id).values(practice_question_id=None)
        )
    await session.delete(question)
    await session.commit()
    return question


async def move_practice_question(session: AsyncSession, question_id: int, offset: int) -> None:
    """Moves a question `offset` places up (-1) / down (+1) in its module's
    list, renumbering the whole module 0..n-1 so ties can't stall a move."""
    question = await session.get(PracticeQuestion, question_id)
    if question is None:
        return
    questions = await get_practice_questions(session, question.module)
    index = next(i for i, q in enumerate(questions) if q.id == question_id)
    target = max(0, min(len(questions) - 1, index + offset))
    questions.insert(target, questions.pop(index))
    for i, q in enumerate(questions):
        q.order_index = i
    await session.commit()


# Practice questions imported from channels (services/practice_import/) -------


async def get_imported_questions(session: AsyncSession, source: str) -> list[PracticeQuestion]:
    stmt = select(PracticeQuestion).where(PracticeQuestion.source == source)
    return list((await session.execute(stmt)).scalars().all())


async def nearest_imported_question(
    session: AsyncSession, module: PracticeModule, source: str, embedding: list[float]
) -> tuple[PracticeQuestion, float] | None:
    """The closest question from `source` in `module`, with its cosine distance."""
    distance = PracticeQuestion.embedding.cosine_distance(embedding)
    stmt = (
        select(PracticeQuestion, distance)
        .where(
            PracticeQuestion.module == module,
            PracticeQuestion.source == source,
            PracticeQuestion.embedding.is_not(None),
        )
        .order_by(distance)
        .limit(1)
    )
    row = (await session.execute(stmt)).first()
    return (row[0], float(row[1])) if row else None


async def order_practice_module_by_recency(session: AsyncSession, module: PracticeModule) -> None:
    """Most recently reported questions first; questions without a date keep
    their order after them."""
    questions = await get_practice_questions(session, module)
    dated = sorted((q for q in questions if q.seen_at), key=lambda q: (q.seen_at, q.id), reverse=True)
    undated = [q for q in questions if not q.seen_at]
    for i, q in enumerate([*dated, *undated]):
        q.order_index = i
    await session.commit()


async def clear_practice_progress(session: AsyncSession, question_id: int) -> None:
    """Drops the ✅ marks of a question whose text changed - it's a new question now."""
    await session.execute(delete(PracticeProgress).where(PracticeProgress.practice_question_id == question_id))


async def get_import_posts(session: AsyncSession, channel: str) -> dict[int, PracticeImportPost]:
    stmt = select(PracticeImportPost).where(PracticeImportPost.channel == channel)
    return {row.post_id: row for row in (await session.execute(stmt)).scalars().all()}


async def mark_import_post(
    session: AsyncSession, channel: str, post_id: int, status: str, note: str | None = None
) -> None:
    row = (
        await session.execute(
            select(PracticeImportPost).where(
                PracticeImportPost.channel == channel, PracticeImportPost.post_id == post_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = PracticeImportPost(channel=channel, post_id=post_id, attempts=0)
        session.add(row)
    row.status = status
    row.note = note
    row.attempts = (row.attempts or 0) + 1
    await session.commit()


async def get_completed_question_ids(
    session: AsyncSession, user_id: int, module: PracticeModule
) -> set[int]:
    stmt = (
        select(PracticeProgress.practice_question_id)
        .join(PracticeQuestion, PracticeQuestion.id == PracticeProgress.practice_question_id)
        .where(PracticeProgress.user_id == user_id, PracticeQuestion.module == module)
    )
    return set((await session.execute(stmt)).scalars().all())


async def mark_practice_complete(
    session: AsyncSession,
    user_id: int,
    practice_question_id: int,
    submission_id: int,
    submission_type: str,
) -> None:
    existing = await session.execute(
        select(PracticeProgress).where(
            PracticeProgress.user_id == user_id,
            PracticeProgress.practice_question_id == practice_question_id,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        row = PracticeProgress(user_id=user_id, practice_question_id=practice_question_id)
        session.add(row)
    row.submission_id = submission_id
    row.submission_type = submission_type
    await session.commit()


# --- My Works (history / statistics) -----------------------------------------


async def get_user_history(session: AsyncSession, user_id: int, limit: int = 20) -> list[dict]:
    """Unified, date-sorted history across writing + speaking submissions."""
    w_stmt = (
        select(WritingSubmission)
        .where(WritingSubmission.user_id == user_id)
        .order_by(WritingSubmission.created_at.desc())
        .limit(limit)
    )
    s_stmt = (
        select(SpeakingSubmission)
        .where(SpeakingSubmission.user_id == user_id)
        .order_by(SpeakingSubmission.created_at.desc())
        .limit(limit)
    )
    w_rows = (await session.execute(w_stmt)).scalars().all()
    s_rows = (await session.execute(s_stmt)).scalars().all()

    combined = [
        {
            "type": "writing",
            "id": r.id,
            "label": f"Writing {r.task_type.value.upper()}",
            "band": r.overall_band,
            "date": r.created_at,
            "pdf_path": r.feedback_pdf_path,
        }
        for r in w_rows
    ] + [
        {
            "type": "speaking",
            "id": r.id,
            "label": f"Speaking {r.part.value.upper()}",
            "band": r.overall_band,
            "date": r.created_at,
            "pdf_path": r.feedback_pdf_path,
        }
        for r in s_rows
    ]
    combined.sort(key=lambda x: x["date"], reverse=True)
    return combined[:limit]


async def get_score_history(session: AsyncSession, user_id: int) -> list[tuple[datetime, float]]:
    """All overall_band scores in chronological order, for the statistics chart."""
    w_stmt = select(WritingSubmission.created_at, WritingSubmission.overall_band).where(
        WritingSubmission.user_id == user_id, WritingSubmission.overall_band.is_not(None)
    )
    s_stmt = select(SpeakingSubmission.created_at, SpeakingSubmission.overall_band).where(
        SpeakingSubmission.user_id == user_id, SpeakingSubmission.overall_band.is_not(None)
    )
    w_rows = (await session.execute(w_stmt)).all()
    s_rows = (await session.execute(s_stmt)).all()
    all_rows = sorted([*w_rows, *s_rows], key=lambda r: r[0])
    return [(row[0], row[1]) for row in all_rows]


# --- Payments (card top-up, admin-reviewed) ----------------------------------


async def create_payment_request(
    session: AsyncSession,
    *,
    user_id: int,
    amount: int,
    credits: int = 0,
    kind: PaymentKind = PaymentKind.credits,
    subscription_months: int | None = None,
) -> PaymentRequest:
    request = PaymentRequest(
        user_id=user_id,
        credits=credits,
        amount=amount,
        kind=kind,
        subscription_months=subscription_months,
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)
    return request


async def set_payment_receipt(
    session: AsyncSession, request_id: int, receipt_file_id: str
) -> PaymentRequest | None:
    request = await session.get(PaymentRequest, request_id)
    if request is None:
        return None
    request.receipt_file_id = receipt_file_id
    await session.commit()
    await session.refresh(request)
    return request


async def decide_payment_request(
    session: AsyncSession, request_id: int, *, approve: bool, admin_id: int
) -> tuple[PaymentRequest | None, bool]:
    """Approves or rejects a payment request, adding credits to the buyer's
    balance on approval. Returns (request, applied) - applied is False when
    the request was already decided (e.g. two admins tapping their own copy
    of the same notification), mirroring the old registration approve/deny
    race guard."""
    request = await session.get(PaymentRequest, request_id)
    if request is None:
        return None, False
    if request.status != PaymentStatus.pending:
        return request, False

    request.status = PaymentStatus.approved if approve else PaymentStatus.rejected
    request.reviewed_at = datetime.now(timezone.utc)
    request.reviewed_by = admin_id
    if approve:
        user = await session.get(User, request.user_id)
        if user is not None:
            if request.kind == PaymentKind.subscription:
                now = datetime.now(timezone.utc)
                base = user.subscription_expires_at if (
                    user.subscription_expires_at and user.subscription_expires_at > now
                ) else now
                user.subscription_expires_at = base + timedelta(
                    days=30 * (request.subscription_months or 1)
                )
            else:
                user.credit_balance += request.credits
    await session.commit()
    await session.refresh(request)
    return request, True


# --- Admin stats (/stats) ------------------------------------------------------

_TASHKENT_OFFSET = timedelta(hours=5)  # no DST in Uzbekistan


def _today_start_tashkent_as_utc() -> datetime:
    today_tashkent = (datetime.now(timezone.utc) + _TASHKENT_OFFSET).date()
    midnight_tashkent = datetime(
        today_tashkent.year, today_tashkent.month, today_tashkent.day, tzinfo=timezone.utc
    )
    return midnight_tashkent - _TASHKENT_OFFSET


async def get_admin_stats(session: AsyncSession) -> dict:
    """A quick snapshot for /stats - today is a Tashkent calendar day, not a
    rolling 24h window (unlike the free-quota check), since that's what an
    admin means by 'bugun'."""
    since = _today_start_tashkent_as_utc()

    new_users = (
        await session.execute(select(func.count()).select_from(User).where(User.created_at >= since))
    ).scalar() or 0
    writing_today = (
        await session.execute(
            select(func.count())
            .select_from(WritingSubmission)
            .where(WritingSubmission.created_at >= since)
        )
    ).scalar() or 0
    speaking_today = (
        await session.execute(
            select(func.count())
            .select_from(SpeakingSubmission)
            .where(SpeakingSubmission.created_at >= since)
        )
    ).scalar() or 0
    revenue_today = (
        await session.execute(
            select(func.coalesce(func.sum(PaymentRequest.amount), 0)).where(
                PaymentRequest.status == PaymentStatus.approved, PaymentRequest.reviewed_at >= since
            )
        )
    ).scalar() or 0
    pending_payments = (
        await session.execute(
            select(func.count())
            .select_from(PaymentRequest)
            .where(PaymentRequest.status == PaymentStatus.pending)
        )
    ).scalar() or 0
    total_users = (await session.execute(select(func.count()).select_from(User))).scalar() or 0

    return {
        "new_users_today": new_users,
        "writing_today": writing_today,
        "speaking_today": speaking_today,
        "revenue_today": revenue_today,
        "pending_payments": pending_payments,
        "total_users": total_users,
    }


# --- Groups (B2B: teachers / language centers) --------------------------------
# A teacher (role `teacher`, granted by an admin - bot/handlers/group.py) owns
# one group; students join through a signed invite link
# (bot/utils/group_invite.py). Everything a teacher sees about a student covers
# only work submitted after the student joined - joining is when they agreed to
# share it.


async def set_user_role(session: AsyncSession, user_id: int, role: UserRole) -> User | None:
    user = await session.get(User, user_id)
    if user is None:
        return None
    user.role = role
    await session.commit()
    return user


async def create_group(session: AsyncSession, teacher_id: int, name: str) -> Group:
    group = Group(teacher_id=teacher_id, name=name)
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


async def get_group(session: AsyncSession, group_id: int) -> Group | None:
    return await session.get(Group, group_id)


async def get_owned_group(session: AsyncSession, teacher_id: int) -> Group | None:
    stmt = select(Group).where(Group.teacher_id == teacher_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_member_groups(session: AsyncSession, user_id: int) -> list[tuple[Group, User | None]]:
    """The groups this user is a student in (newest first), each with its teacher."""
    stmt = (
        select(Group, User)
        .join(GroupMembership, GroupMembership.group_id == Group.id)
        .outerjoin(User, User.id == Group.teacher_id)
        .where(GroupMembership.user_id == user_id)
        .order_by(GroupMembership.joined_at.desc())
    )
    return [(group, teacher) for group, teacher in (await session.execute(stmt)).all()]


async def get_membership(session: AsyncSession, group_id: int, user_id: int) -> GroupMembership | None:
    stmt = select(GroupMembership).where(
        GroupMembership.group_id == group_id, GroupMembership.user_id == user_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def join_group(session: AsyncSession, group_id: int, user_id: int) -> bool:
    """True when a new membership was created; False when the user is already
    in the group or is its own teacher (a teacher opening their own invite
    link must not end up as a student of their group)."""
    group = await session.get(Group, group_id)
    if group is None or group.teacher_id == user_id:
        return False
    if await get_membership(session, group_id, user_id) is not None:
        return False
    session.add(GroupMembership(group_id=group_id, user_id=user_id))
    await session.commit()
    return True


async def leave_group(session: AsyncSession, group_id: int, user_id: int) -> bool:
    """Removes one membership (a student leaving, or their teacher removing them)."""
    result = await session.execute(
        delete(GroupMembership).where(
            GroupMembership.group_id == group_id, GroupMembership.user_id == user_id
        )
    )
    await session.commit()
    return result.rowcount > 0


async def delete_group(session: AsyncSession, group_id: int) -> None:
    """Deletes a group and its memberships; students keep their work and credits."""
    await session.execute(delete(GroupMembership).where(GroupMembership.group_id == group_id))
    await session.execute(delete(Group).where(Group.id == group_id))
    await session.commit()


async def get_group_member_count(session: AsyncSession, group_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(GroupMembership).where(GroupMembership.group_id == group_id)
    )
    return result.scalar() or 0


async def get_group_members(
    session: AsyncSession, group_id: int, offset: int = 0, limit: int | None = None
) -> list[tuple[User, datetime]]:
    """(student, joined_at) pairs in joining order - all of them unless paged."""
    stmt = (
        select(User, GroupMembership.joined_at)
        .join(GroupMembership, GroupMembership.user_id == User.id)
        .where(GroupMembership.group_id == group_id)
        .order_by(GroupMembership.joined_at, User.id)
        .offset(offset)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return [(user, joined_at) for user, joined_at in (await session.execute(stmt)).all()]


async def get_works_since(session: AsyncSession, user_id: int, since: datetime) -> list[dict]:
    """A student's graded Writing+Speaking work from `since` on, newest first."""
    writing = (
        await session.execute(
            select(WritingSubmission).where(
                WritingSubmission.user_id == user_id,
                WritingSubmission.overall_band.is_not(None),
                WritingSubmission.created_at >= since,
            )
        )
    ).scalars().all()
    speaking = (
        await session.execute(
            select(SpeakingSubmission).where(
                SpeakingSubmission.user_id == user_id,
                SpeakingSubmission.overall_band.is_not(None),
                SpeakingSubmission.created_at >= since,
            )
        )
    ).scalars().all()
    works = [
        {
            "kind": "w",
            "id": w.id,
            "label": f"Writing Task {w.task_type.value[-1]}",
            "band": w.overall_band,
            "criteria": w.criteria_scores or {},
            "created_at": w.created_at,
            "has_pdf": bool(w.feedback_pdf_path),
        }
        for w in writing
    ] + [
        {
            "kind": "s",
            "id": s.id,
            "label": f"Speaking Part {s.part.value[-1]}",
            "band": s.overall_band,
            "criteria": s.criteria_scores or {},
            "created_at": s.created_at,
            "has_pdf": bool(s.feedback_pdf_path),
        }
        for s in speaking
    ]
    return sorted(works, key=lambda w: w["created_at"], reverse=True)


def summarize_works(works: list[dict]) -> dict:
    """Counts, average bands per skill, the weakest criterion (key, average)
    and the last activity date of a list from get_works_since."""

    def average(items: list[dict]) -> float | None:
        return sum(w["band"] for w in items) / len(items) if items else None

    writing = [w for w in works if w["kind"] == "w"]
    speaking = [w for w in works if w["kind"] == "s"]
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for work in works:
        for key, value in work["criteria"].items():
            if value is not None:
                sums[key] = sums.get(key, 0.0) + value
                counts[key] = counts.get(key, 0) + 1
    criteria = {key: sums[key] / counts[key] for key in sums}
    return {
        "total": len(works),
        "average": average(works),
        "writing": len(writing),
        "writing_avg": average(writing),
        "speaking": len(speaking),
        "speaking_avg": average(speaking),
        "weakest": min(criteria.items(), key=lambda kv: kv[1]) if criteria else None,
        "last_active": max((w["created_at"] for w in works), default=None),
    }


async def get_group_overview(session: AsyncSession, group_id: int) -> dict:
    members = await get_group_members(session, group_id)
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    per_student = []
    all_works: list[dict] = []
    for user, joined_at in members:
        works = await get_works_since(session, user.id, joined_at)
        all_works += works
        per_student.append((user, summarize_works(works)))
    most_active = sorted((p for p in per_student if p[1]["total"]), key=lambda p: -p[1]["total"])[:3]
    return {
        "members": len(members),
        "active_week": sum(1 for _, s in per_student if s["last_active"] and s["last_active"] >= week_ago),
        "inactive": sum(1 for _, s in per_student if not s["total"]),
        "summary": summarize_works(all_works),
        "most_active": [(user.full_name, s["total"]) for user, s in most_active],
    }


async def get_member_report_pdf(
    session: AsyncSession, teacher_id: int, kind: str, submission_id: int
) -> str | None:
    """Path of a student's report PDF - only for the teacher of a group the
    student is in, and only for work submitted after they joined it."""
    model = WritingSubmission if kind == "w" else SpeakingSubmission
    submission = await session.get(model, submission_id)
    group = await get_owned_group(session, teacher_id)
    if submission is None or group is None or not submission.feedback_pdf_path:
        return None
    membership = await get_membership(session, group.id, submission.user_id)
    if membership is None or submission.created_at < membership.joined_at:
        return None
    return submission.feedback_pdf_path


async def gift_credits(
    session: AsyncSession, *, teacher_id: int, student_ids: list[int], amount: int
) -> list[User] | None:
    """Moves `amount` credits from the teacher's own balance to each student in
    `student_ids`, all in one transaction - only if every one of them is in
    the teacher's group and the balance covers them all. Returns the students
    credited, or None if nothing was transferred."""
    group = await get_owned_group(session, teacher_id)
    if group is None or amount <= 0 or not student_ids:
        return None
    members = set(
        (
            await session.execute(
                select(GroupMembership.user_id).where(
                    GroupMembership.group_id == group.id, GroupMembership.user_id.in_(student_ids)
                )
            )
        ).scalars().all()
    )
    if members != set(student_ids):
        return None
    # Row locks: two quick taps must not both spend the same balance.
    teacher = (
        await session.execute(select(User).where(User.id == teacher_id).with_for_update())
    ).scalar_one_or_none()
    if teacher is None or teacher.credit_balance < amount * len(members):
        await session.rollback()
        return None
    students = list(
        (await session.execute(select(User).where(User.id.in_(members)).with_for_update())).scalars().all()
    )
    teacher.credit_balance -= amount * len(students)
    for student in students:
        student.credit_balance += amount
    await session.commit()
    return students


# --- Web admin panel (webapp/main.py) ------------------------------------------


async def count_active_subscriptions(session: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(func.count())
        .select_from(User)
        .where(User.subscription_expires_at.is_not(None), User.subscription_expires_at > now)
    )
    return result.scalar() or 0


async def list_users(session: AsyncSession, search: str | None = None, limit: int = 300) -> list[User]:
    stmt = select(User)
    search = (search or "").strip()
    if search:
        conditions = [User.full_name.ilike(f"%{search}%"), User.username.ilike(f"%{search}%")]
        if search.isdigit():
            conditions.append(User.id == int(search))
        stmt = stmt.where(or_(*conditions))
    stmt = stmt.order_by(User.created_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def list_payment_requests(
    session: AsyncSession, status: PaymentStatus | None = None, limit: int = 200
) -> list[PaymentRequest]:
    stmt = select(PaymentRequest).order_by(PaymentRequest.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(PaymentRequest.status == status)
    return list((await session.execute(stmt)).scalars().all())


async def list_groups_with_counts(session: AsyncSession) -> list[dict]:
    groups = (await session.execute(select(Group).order_by(Group.created_at.desc()))).scalars().all()
    result = []
    for group in groups:
        teacher = await session.get(User, group.teacher_id)
        count = await get_group_member_count(session, group.id)
        result.append(
            {
                "id": group.id,
                "name": group.name,
                "teacher_name": teacher.full_name if teacher is not None else "?",
                "teacher_id": group.teacher_id,
                "member_count": count,
                "created_at": group.created_at,
            }
        )
    return result


async def get_dashboard_timeseries(session: AsyncSession, days: int = 30) -> list[dict]:
    """One row per calendar day (Tashkent time, oldest first) for the last
    `days` days: new users, Writing/Speaking submission counts, and so'm
    revenue from payments approved that day - the data behind the
    dashboard's charts."""
    since = _today_start_tashkent_as_utc() - timedelta(days=days - 1)

    users = (
        await session.execute(select(User.created_at).where(User.created_at >= since))
    ).scalars().all()
    writing = (
        await session.execute(
            select(WritingSubmission.created_at).where(WritingSubmission.created_at >= since)
        )
    ).scalars().all()
    speaking = (
        await session.execute(
            select(SpeakingSubmission.created_at).where(SpeakingSubmission.created_at >= since)
        )
    ).scalars().all()
    payments = (
        await session.execute(
            select(PaymentRequest.reviewed_at, PaymentRequest.amount).where(
                PaymentRequest.status == PaymentStatus.approved, PaymentRequest.reviewed_at >= since
            )
        )
    ).all()

    def day_of(dt: datetime):
        return (dt + _TASHKENT_OFFSET).date()

    rows = []
    for i in range(days):
        day = day_of(since + timedelta(days=i))
        rows.append(
            {
                "date": day.strftime("%m-%d"),
                "new_users": sum(1 for u in users if day_of(u) == day),
                "writing": sum(1 for w in writing if day_of(w) == day),
                "speaking": sum(1 for s in speaking if day_of(s) == day),
                "revenue": sum(amt for dt, amt in payments if dt is not None and day_of(dt) == day),
            }
        )
    return rows
