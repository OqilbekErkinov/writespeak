"""Small, focused query helpers used by handlers/services.

Kept deliberately thin (no repository abstraction) — each function takes an
already-open AsyncSession so callers control the transaction boundary via
`db.database.get_session()`.
"""
from __future__ import annotations

import random
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from db.models import (
    Assignment,
    AssignmentPart,
    Center,
    CenterTeacher,
    CoinAdjustment,
    CoinGift,
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
    FULL_TEST_CHECKS,
    SPEAKING_LABELS,
    SpeakingPart,
    SpeakingSubmission,
    SubmissionComment,
    User,
    UserRole,
    UserStatus,
    VocabularyEntry,
    WritingSubmission,
)
from services.ai import usage
from services.ai.essay_marking import count_words
from services.ai.schemas import VocabularyItem

# --- Users / access control -------------------------------------------------


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def create_user(
    session: AsyncSession,
    user_id: int,
    username: str | None,
    full_name: str,
    referred_by: int | None = None,
    coins: int = 0,
) -> User:
    """The bot is open to anyone - no admin approval gate - so every new
    signup is created active right away, with `coins` (the welcome gift).
    `referred_by` is the referrer's Telegram id from a `/start ref_<id>` deep
    link; both sides are only rewarded on this user's first approved coin
    purchase (see decide_payment_request), not just for signing up."""
    user = User(
        id=user_id,
        username=username,
        full_name=full_name,
        status=UserStatus.approved,
        approved_at=datetime.now(timezone.utc),
        referred_by=referred_by if referred_by != user_id else None,
        coin_balance=coins,
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
    coins: int = 0,
    assignment_part_id: int | None = None,
    payer_id: int | None = None,
    report_data: dict | None = None,
) -> WritingSubmission:
    """`annotations` holds the report's numbered mistakes (each with its
    `criterion`) since the 2026-10 report redesign - get_writing_history
    compares their per-criterion counts with the student's next essay.
    Older rows hold the previous sentence-annotation format. `coins` (the
    check's price, 0 when free) are spent from `payer_id` (a group's
    sponsor) when given, else from the student."""
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
        report_data=report_data,
        feedback_pdf_path=feedback_pdf_path,
        practice_question_id=practice_question_id,
        assignment_part_id=assignment_part_id,
        paid_by_id=payer_id if coins and payer_id else None,
        coins_spent=coins,
    )
    session.add(submission)
    if coins:
        await _spend_coins(session, payer_id or user_id, coins)
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
    overall_band: float,
    criteria_scores: dict[str, float],
    mistakes: list[dict],
    vocabulary: list[VocabularyItem],
    report_data: dict,
    feedback_pdf_path: str,
    practice_question_id: int | None = None,
    coins: int = 0,
    assignment_part_id: int | None = None,
    payer_id: int | None = None,
) -> SpeakingSubmission:
    """`coins` (the check's price, 0 when free - bot/utils/coins.py) are
    spent from `payer_id` (a group's sponsor) when given, else from the
    student."""
    submission = SpeakingSubmission(
        user_id=user_id,
        part=part,
        question_text=question_text,
        audio_file_path=audio_file_path,
        transcript_text=transcript_text,
        overall_band=overall_band,
        criteria_scores=criteria_scores,
        annotations=mistakes,
        vocabulary_json=[v.model_dump() for v in vocabulary],
        report_data=report_data,
        feedback_pdf_path=feedback_pdf_path,
        practice_question_id=practice_question_id,
        assignment_part_id=assignment_part_id,
        paid_by_id=payer_id if coins and payer_id else None,
        coins_spent=coins,
    )
    session.add(submission)
    if coins:
        await _spend_coins(session, payer_id or user_id, coins)
    await session.commit()
    await session.refresh(submission)
    return submission


async def save_model_answer_voice(
    session: AsyncSession, submission_id: int, index: int, file_id: str | None, cost: dict
) -> None:
    """After a model answer was spoken on demand: Telegram's file id (reused
    next time) and the voice's cost, added to the check's cost."""
    submission = await session.get(SpeakingSubmission, submission_id)
    if submission is None:
        return
    data = dict(submission.report_data or {})
    answers = [dict(a) for a in data.get("model_answers") or []]
    if 0 <= index < len(answers) and file_id:
        answers[index]["voice_file_id"] = file_id
    data["model_answers"] = answers
    data["cost"] = usage.merge(data.get("cost"), cost)
    submission.report_data = data  # a new dict, so SQLAlchemy sees the change
    await session.commit()


async def get_speaking_history(session: AsyncSession, user_id: int, part: str) -> tuple[int, dict | None]:
    """(number of the student's earlier Speaking sessions, their latest graded
    session of the same part as {"id", "band", "metrics", "number"}) for the
    report's progress page. metrics is None for sessions graded before the
    2026-10-13 report, which measured no voice."""
    count = (
        await session.execute(
            select(func.count()).select_from(SpeakingSubmission).where(SpeakingSubmission.user_id == user_id)
        )
    ).scalar() or 0
    latest = (
        await session.execute(
            select(SpeakingSubmission)
            .where(
                SpeakingSubmission.user_id == user_id,
                SpeakingSubmission.part == part,
                SpeakingSubmission.overall_band.is_not(None),
            )
            .order_by(SpeakingSubmission.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        return count, None
    data = latest.report_data or {}
    return count, {"id": latest.id, "band": latest.overall_band, "metrics": data.get("metrics"),
                   "number": data.get("number")}


async def get_speaking_sessions(session: AsyncSession, user_id: int, part: str, limit: int = 20) -> list[SpeakingSubmission]:
    """The student's latest graded sessions of one part, newest first - for
    spotting a resubmission of the same answers."""
    stmt = (
        select(SpeakingSubmission)
        .where(SpeakingSubmission.user_id == user_id, SpeakingSubmission.part == part,
               SpeakingSubmission.overall_band.is_not(None))
        .order_by(SpeakingSubmission.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_writing_sessions(session: AsyncSession, user_id: int, task_type: str, limit: int = 20) -> list[WritingSubmission]:
    """The student's latest graded essays of one task type, newest first - for
    spotting a resubmission of the same essay."""
    stmt = (
        select(WritingSubmission)
        .where(WritingSubmission.user_id == user_id, WritingSubmission.task_type == task_type,
               WritingSubmission.overall_band.is_not(None))
        .order_by(WritingSubmission.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def writing_essay_number(session: AsyncSession, user_id: int, submission_id: int) -> int:
    """The "Essay #N" a Writing report was given: the student's Nth essay."""
    return (await session.execute(
        select(func.count()).select_from(WritingSubmission).where(
            WritingSubmission.user_id == user_id, WritingSubmission.id <= submission_id)
    )).scalar() or 0


async def get_writing_history(
    session: AsyncSession, user_id: int, task_type: str, min_words: int = 0
) -> tuple[int, dict | None]:
    """(number of the student's earlier Writing submissions, their latest
    graded one of the same task type with at least `min_words` words as
    {"band", "criteria_scores", "mistake_counts", "words"}) for the report's
    essay number and progress page - a 46-word test answer is no "previous
    essay" (#48 was compared with #47's Band 3.5); {"too_short": True} when
    every earlier one is that short. mistake_counts is None for
    reports made before the 2026-10 redesign, whose annotations carry no
    criterion; words comes from the stored answer text."""
    count = (
        await session.execute(
            select(func.count()).select_from(WritingSubmission).where(WritingSubmission.user_id == user_id)
        )
    ).scalar() or 0
    recent = (
        await session.execute(
            select(WritingSubmission)
            .where(
                WritingSubmission.user_id == user_id,
                WritingSubmission.task_type == task_type,
                WritingSubmission.overall_band.is_not(None),
            )
            .order_by(WritingSubmission.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    latest = next((s for s in recent if count_words(s.answer_text or "") >= min_words), None)
    if latest is None:
        # {"too_short": True}: there were earlier ones, all too short to compare with
        return count, ({"too_short": True} if recent else None)
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


async def _spend_coins(session: AsyncSession, user_id: int, coins: int) -> None:
    """Takes a check's price off the payer's balance (never below 0: the
    balance was checked when the check started - bot/utils/quota.py)."""
    user = (await session.execute(select(User).where(User.id == user_id).with_for_update())).scalar_one_or_none()
    if user is not None:
        user.coin_balance = max(0, user.coin_balance - coins)


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
    """Lifetime count of delivered checks (a full Speaking test counts
    FULL_TEST_CHECKS)."""
    w = await session.execute(
        select(func.count()).select_from(WritingSubmission).where(WritingSubmission.user_id == user_id)
    )
    s = await session.execute(
        select(_speaking_checks()).select_from(SpeakingSubmission).where(SpeakingSubmission.user_id == user_id)
    )
    return (w.scalar() or 0) + (s.scalar() or 0)


def _speaking_checks():
    """Checks used by Speaking rows: a full test counts FULL_TEST_CHECKS."""
    return func.coalesce(
        func.sum(case((SpeakingSubmission.part == SpeakingPart.full, FULL_TEST_CHECKS), else_=1)), 0
    )


def _checks(model):
    return func.count() if model is WritingSubmission else _speaking_checks()


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
CRITERIA_TIPS_RU = {
    "TA": "Отвечайте на все части вопроса и сразу формулируйте свою позицию во вступлении.",
    "CC": "Связывайте абзацы и идеи (however, therefore, in addition); одна главная мысль на абзац.",
    "LR": "Учите тематические словосочетания и используйте их вместо общих слов.",
    "GRA": "Чередуйте простые и сложные предложения и проверяйте времена и артикли.",
    "FC": "Говорите дольше без пауз: тренируйтесь отвечать 1-2 минуты на один вопрос.",
    "Pronunciation": "Слушайте носителей и повторяйте за ними ударение и интонацию.",
}
CRITERIA_TIPS_EN = {
    "TA": "Answer every part of the question in full and state your main position clearly "
    "in the introduction.",
    "CC": "Link paragraphs and ideas with linking words (however, therefore, in addition); "
    "keep one main idea per paragraph.",
    "LR": "Use the words from \"📚 My vocabulary\" in your next answers, and try replacing "
    "simple words with synonyms.",
    "GRA": "Use a variety of sentence structures (complex sentences, conditionals) - more "
    "practice in the Practice section helps.",
    "FC": "Pause naturally while you answer and finish every sentence - don't drop an idea "
    "halfway.",
    "Pronunciation": "Pronounce words more clearly and mind sentence stress - practising "
    "at a slower pace improves pronunciation.",
}


def criteria_tip(key: str, lang: str) -> str:
    """The advice for a (weakest) criterion in the reader's language."""
    tips = {"ru": CRITERIA_TIPS_RU, "en": CRITERIA_TIPS_EN}.get(lang, CRITERIA_TIPS)
    return tips.get(key, "")


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
    session: AsyncSession, current: PracticeQuestion, user_id: int
) -> PracticeQuestion | None:
    """The first question after `current` in its module's list order -
    wrapping round to the top - that the student hasn't done yet, if any."""
    questions = await get_practice_questions(session, current.module)
    done = await get_completed_question_ids(session, user_id, current.module)
    index = next((i for i, q in enumerate(questions) if q.id == current.id), -1)
    for question in questions[index + 1 :] + questions[: max(index, 0)]:
        if question.id not in done:
            return question
    return None


async def get_practice_stats(session: AsyncSession, user_id: int) -> dict[PracticeModule, tuple[int, int]]:
    """(questions done, questions in all) per module, for the module picker."""
    totals = await get_practice_question_counts(session)
    stmt = (
        select(PracticeQuestion.module, func.count())
        .join(PracticeProgress, PracticeProgress.practice_question_id == PracticeQuestion.id)
        .where(PracticeProgress.user_id == user_id)
        .group_by(PracticeQuestion.module)
    )
    done = dict((await session.execute(stmt)).all())
    return {module: (done.get(module, 0), totals.get(module, 0)) for module in PracticeModule}


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


async def nearest_imported_questions(
    session: AsyncSession,
    module: PracticeModule,
    source: str,
    embedding: list[float],
    *,
    limit: int,
    max_distance: float,
) -> list[tuple[PracticeQuestion, float]]:
    """The questions from `source` in `module` closest to `embedding`, with
    their cosine distance, closest first."""
    distance = PracticeQuestion.embedding.cosine_distance(embedding)
    stmt = (
        select(PracticeQuestion, distance)
        .where(
            PracticeQuestion.module == module,
            PracticeQuestion.source == source,
            PracticeQuestion.embedding.is_not(None),
            distance <= max_distance,
        )
        .order_by(distance)
        .limit(limit)
    )
    return [(row[0], float(row[1])) for row in (await session.execute(stmt)).all()]


async def merge_practice_questions(session: AsyncSession, keep_id: int, drop_id: int) -> str | None:
    """Folds a copy of a question into the one kept: times seen add up, the
    latest date wins, ✅ marks and past submissions move over, the copy goes.
    Returns the copy's image path if nothing uses it any more (for the
    caller to delete, like delete_practice_question)."""
    keep = await session.get(PracticeQuestion, keep_id)
    drop = await session.get(PracticeQuestion, drop_id)
    if keep is None or drop is None:
        return None
    keep.times_seen = (keep.times_seen or 1) + (drop.times_seen or 1)
    if drop.seen_at and (keep.seen_at is None or drop.seen_at > keep.seen_at):
        keep.seen_at = drop.seen_at
    leftover_image = drop.image_path
    if not keep.image_path and drop.image_path:
        keep.image_path, leftover_image = drop.image_path, None
    already_done = select(PracticeProgress.user_id).where(PracticeProgress.practice_question_id == keep_id)
    await session.execute(
        delete(PracticeProgress).where(
            PracticeProgress.practice_question_id == drop_id, PracticeProgress.user_id.in_(already_done)
        )
    )
    await session.execute(
        update(PracticeProgress).where(PracticeProgress.practice_question_id == drop_id).values(practice_question_id=keep_id)
    )
    for model in (WritingSubmission, SpeakingSubmission):
        await session.execute(
            update(model).where(model.practice_question_id == drop_id).values(practice_question_id=keep_id)
        )
    await session.delete(drop)
    await session.commit()
    return leftover_image


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
            "label": f"Writing Task {r.task_type.value[-1]}",
            "band": r.overall_band,
            "date": r.created_at,
            "pdf_path": r.feedback_pdf_path,
        }
        for r in w_rows
    ] + [
        {
            "type": "speaking",
            "id": r.id,
            "label": SPEAKING_LABELS.get(r.part.value, "Speaking"),
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
    coins: int = 0,
    kind: PaymentKind = PaymentKind.credits,
    subscription_months: int | None = None,
) -> PaymentRequest:
    request = PaymentRequest(
        user_id=user_id,
        coins=coins,
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


@dataclass
class PaymentDecision:
    request: PaymentRequest | None
    applied: bool  # False when it had already been decided (or doesn't exist)
    referrer_id: int | None = None  # paid the referral bonus with this approval
    gift: CoinGift | None = None  # an approved gift payment's link (PaymentKind.gift)


async def _locked_user(session: AsyncSession, user_id: int) -> User | None:
    """The user row, locked (FOR UPDATE) and freshly read, before changing
    coin_balance: the ORM writes the new value, not `balance + n`."""
    return (await session.execute(
        select(User).where(User.id == user_id).with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()


async def decide_payment_request(
    session: AsyncSession, request_id: int, *, approve: bool, admin_id: int, referral_coins: int = 0
) -> PaymentDecision:
    """Approves or rejects a payment request, adding the coins to the
    buyer's balance on approval. `applied` is False when the request was
    already decided (e.g. two admins tapping their own copy of the same
    notification), mirroring the old registration approve/deny race guard.
    The buyer's first approved coin purchase also pays `referral_coins` to
    them and to whoever invited them (once - User.referral_rewarded). A gift
    purchase counts as that purchase too (it is their money), but its coins
    go to a one-time gift link, not to the buyer.

    Row locks (request, buyer, inviter): approval exists both in the bot and
    in the admin panel, and the ORM writes balances as absolute values - two
    admins at once must not both credit, nor overwrite a check's charge."""
    request = (await session.execute(
        select(PaymentRequest).where(PaymentRequest.id == request_id).with_for_update()
    )).scalar_one_or_none()
    if request is None:
        return PaymentDecision(None, False)
    if request.status != PaymentStatus.pending:
        return PaymentDecision(request, False)
    referrer_id = None
    gift = None

    request.status = PaymentStatus.approved if approve else PaymentStatus.rejected
    request.reviewed_at = datetime.now(timezone.utc)
    request.reviewed_by = admin_id
    if approve:
        user = await _locked_user(session, request.user_id)
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
                if request.kind == PaymentKind.gift:
                    gift = CoinGift(code=secrets.token_urlsafe(9), coins=request.coins, buyer_id=user.id,
                                    payment_request_id=request.id)
                    session.add(gift)
                else:
                    user.coin_balance += request.coins
                if referral_coins and user.referred_by and not user.referral_rewarded:
                    referrer = await _locked_user(session, user.referred_by)
                    if referrer is not None:
                        referrer.coin_balance += referral_coins
                        user.coin_balance += referral_coins
                        referrer_id = referrer.id
                    user.referral_rewarded = True
    await session.commit()
    await session.refresh(request)
    if gift is not None:
        await session.refresh(gift)
    return PaymentDecision(request, True, referrer_id, gift)


# --- Coin gifts (bot/handlers/payments.py buys, bot/handlers/start.py claims) ---


async def get_gift(session: AsyncSession, code: str) -> CoinGift | None:
    return (await session.execute(select(CoinGift).where(CoinGift.code == code))).scalar_one_or_none()


@dataclass
class GiftClaim:
    status: str  # "claimed", "own" (the buyer's own link), "taken" (claimed before), "missing"
    gift: CoinGift | None = None
    buyer: User | None = None
    balance: int = 0  # the claimer's balance after a claim


async def claim_gift(session: AsyncSession, code: str, user_id: int) -> GiftClaim:
    """Gives a gift's coins to `user_id`, once: the row is locked, so two
    people opening the same link at the same moment can't both get them."""
    gift = (await session.execute(
        select(CoinGift).where(CoinGift.code == code).with_for_update()
    )).scalar_one_or_none()
    if gift is None:
        return GiftClaim("missing")
    buyer = await session.get(User, gift.buyer_id)
    if gift.buyer_id == user_id:
        return GiftClaim("own", gift, buyer)
    if gift.claimed_by is not None:
        return GiftClaim("taken", gift, buyer)
    user = await _locked_user(session, user_id)  # a check's charge may be committing right now
    if user is None:
        return GiftClaim("missing")
    user.coin_balance += gift.coins
    gift.claimed_by = user_id
    gift.claimed_at = datetime.now(timezone.utc)
    await session.commit()
    return GiftClaim("claimed", gift, buyer, user.coin_balance)


async def unclaimed_gifts(session: AsyncSession, buyer_id: int, limit: int = 5) -> list[CoinGift]:
    stmt = (select(CoinGift).where(CoinGift.buyer_id == buyer_id, CoinGift.claimed_by.is_(None))
            .order_by(CoinGift.created_at.desc()).limit(limit))
    return list((await session.execute(stmt)).scalars().all())


# --- Coins changed by hand in the admin panel (webapp/main.py) -----------------


async def adjust_coins(session: AsyncSession, user_id: int, delta: int, note: str | None) -> CoinAdjustment | None:
    """Adds `delta` coins (negative: takes them away) to a user's balance and
    records it. None when there's no such user or the balance would go below 0."""
    user = await session.get(User, user_id, with_for_update=True)
    if user is None or delta == 0 or user.coin_balance + delta < 0:
        return None
    user.coin_balance += delta
    adjustment = CoinAdjustment(user_id=user_id, delta=delta, balance_after=user.coin_balance, note=note or None)
    session.add(adjustment)
    await session.commit()
    await session.refresh(adjustment)
    return adjustment


async def list_coin_adjustments(session: AsyncSession, user_id: int, limit: int = 20) -> list[CoinAdjustment]:
    stmt = (select(CoinAdjustment).where(CoinAdjustment.user_id == user_id)
            .order_by(CoinAdjustment.created_at.desc()).limit(limit))
    return list((await session.execute(stmt)).scalars().all())


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


# --- Groups, centers, assignments (B2B: teachers / learning centers) ------------
# A teacher (role `teacher`, granted by an admin or by joining a center) runs
# one or more groups; students join through a signed invite link
# (bot/utils/group_invite.py). A center's owner manages every group of the
# center and pays for it. Everything a teacher sees about a student covers only
# work submitted after the student joined - joining is when they agreed to
# share it.

MOCK_PARTS = (
    PracticeModule.writing_task1,
    PracticeModule.writing_task2,
    PracticeModule.speaking_part1,
    PracticeModule.speaking_part2,
    PracticeModule.speaking_part3,
)


def week_start_tashkent_as_utc(weeks_ago: int = 0) -> datetime:
    """Monday 00:00 Tashkent of this week (or `weeks_ago` weeks before), in UTC."""
    today = _today_start_tashkent_as_utc()
    weekday = (today + _TASHKENT_OFFSET).weekday()
    return today - timedelta(days=weekday + 7 * weeks_ago)


async def set_user_role(session: AsyncSession, user_id: int, role: UserRole) -> User | None:
    user = await session.get(User, user_id)
    if user is None:
        return None
    user.role = role
    await session.commit()
    return user


# --- centers ---


async def get_center(session: AsyncSession, center_id: int) -> Center | None:
    return await session.get(Center, center_id)


async def get_owned_center(session: AsyncSession, user_id: int) -> Center | None:
    return (await session.execute(select(Center).where(Center.owner_id == user_id))).scalar_one_or_none()


async def get_user_center(session: AsyncSession, user_id: int) -> Center | None:
    """The center this user owns or teaches at, if any."""
    owned = await get_owned_center(session, user_id)
    if owned is not None:
        return owned
    stmt = select(Center).join(CenterTeacher, CenterTeacher.center_id == Center.id).where(CenterTeacher.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def create_center(session: AsyncSession, owner_id: int, name: str) -> Center:
    """Makes `owner_id` a center owner (and a teacher); their groups join the center."""
    center = Center(owner_id=owner_id, name=name)
    session.add(center)
    await session.flush()
    owner = await session.get(User, owner_id)
    if owner is not None and owner.role == UserRole.student:
        owner.role = UserRole.teacher
    await session.execute(delete(CenterTeacher).where(CenterTeacher.user_id == owner_id))
    await session.execute(update(Group).where(Group.teacher_id == owner_id).values(center_id=center.id))
    await session.commit()
    await session.refresh(center)
    return center


async def delete_center(session: AsyncSession, center_id: int) -> None:
    """Groups stay with their teachers; only the center link goes."""
    await session.execute(update(Group).where(Group.center_id == center_id).values(center_id=None))
    await session.execute(delete(CenterTeacher).where(CenterTeacher.center_id == center_id))
    await session.execute(delete(Center).where(Center.id == center_id))
    await session.commit()


async def join_center(session: AsyncSession, center_id: int, user_id: int) -> str:
    """"joined" | "already" | "owner" | "other" (teaches at another center).
    Joining makes a student a teacher and moves their groups into the center."""
    center = await session.get(Center, center_id)
    if center is None:
        return "other"
    if center.owner_id == user_id:
        return "owner"
    if await get_owned_center(session, user_id) is not None:
        return "other"
    existing = (await session.execute(select(CenterTeacher).where(CenterTeacher.user_id == user_id))).scalar_one_or_none()
    if existing is not None:
        return "already" if existing.center_id == center_id else "other"
    session.add(CenterTeacher(center_id=center_id, user_id=user_id))
    user = await session.get(User, user_id)
    if user is not None and user.role == UserRole.student:
        user.role = UserRole.teacher
    await session.execute(update(Group).where(Group.teacher_id == user_id).values(center_id=center_id))
    await session.commit()
    return "joined"


async def remove_center_teacher(session: AsyncSession, center_id: int, user_id: int) -> bool:
    result = await session.execute(
        delete(CenterTeacher).where(CenterTeacher.center_id == center_id, CenterTeacher.user_id == user_id)
    )
    await session.execute(
        update(Group).where(Group.teacher_id == user_id, Group.center_id == center_id).values(center_id=None)
    )
    await session.commit()
    return result.rowcount > 0


async def list_center_teachers(session: AsyncSession, center_id: int) -> list[tuple[User, int]]:
    """(teacher, number of their groups in the center) - the owner first."""
    center = await session.get(Center, center_id)
    ids = [center.owner_id] if center else []
    ids += list((await session.execute(
        select(CenterTeacher.user_id).where(CenterTeacher.center_id == center_id).order_by(CenterTeacher.joined_at)
    )).scalars().all())
    result = []
    for user_id in ids:
        user = await session.get(User, user_id)
        count = (await session.execute(
            select(func.count()).select_from(Group).where(Group.teacher_id == user_id, Group.center_id == center_id)
        )).scalar() or 0
        if user is not None:
            result.append((user, count))
    return result


# --- groups ---


async def create_group(session: AsyncSession, teacher_id: int, name: str) -> Group:
    center = await get_user_center(session, teacher_id)
    group = Group(teacher_id=teacher_id, name=name, center_id=center.id if center else None)
    session.add(group)
    await session.commit()
    await session.refresh(group)
    return group


async def get_group(session: AsyncSession, group_id: int) -> Group | None:
    return await session.get(Group, group_id)


async def get_managed_groups(session: AsyncSession, user_id: int) -> list[Group]:
    """Groups this user teaches, plus every group of the center they own."""
    conditions = [Group.teacher_id == user_id]
    center = await get_owned_center(session, user_id)
    if center is not None:
        conditions.append(Group.center_id == center.id)
    stmt = select(Group).where(or_(*conditions)).order_by(Group.created_at)
    return list((await session.execute(stmt)).scalars().all())


async def can_manage_group(session: AsyncSession, user_id: int, group: Group | None) -> bool:
    if group is None:
        return False
    if group.teacher_id == user_id:
        return True
    if group.center_id is not None:
        center = await session.get(Center, group.center_id)
        return center is not None and center.owner_id == user_id
    return False


async def get_group_payer_id(session: AsyncSession, group: Group) -> int:
    """Whose coins pay for the group's gifts and sponsored checks."""
    if group.center_id is not None:
        center = await session.get(Center, group.center_id)
        if center is not None:
            return center.owner_id
    return group.teacher_id


async def update_group(session: AsyncSession, group_id: int, **fields) -> Group | None:
    group = await session.get(Group, group_id)
    if group is None:
        return None
    for key, value in fields.items():
        setattr(group, key, value)
    await session.commit()
    return group


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
    """Deletes a group, its memberships and assignments; students keep their
    work and coins (their submissions just lose the assignment link)."""
    part_ids = select(AssignmentPart.id).join(Assignment, Assignment.id == AssignmentPart.assignment_id).where(
        Assignment.group_id == group_id
    )
    for model in (WritingSubmission, SpeakingSubmission):
        await session.execute(update(model).where(model.assignment_part_id.in_(part_ids)).values(assignment_part_id=None))
    assignment_ids = select(Assignment.id).where(Assignment.group_id == group_id)
    await session.execute(delete(AssignmentPart).where(AssignmentPart.assignment_id.in_(assignment_ids)))
    await session.execute(delete(Assignment).where(Assignment.group_id == group_id))
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


# --- a student's work as their teacher sees it ---


def work_info(kind: str, row) -> dict:
    if kind == "w":
        label = f"Writing Task {row.task_type.value[-1]}"
    else:
        label = SPEAKING_LABELS.get(row.part.value, "Speaking")
    return {
        "kind": kind,
        "id": row.id,
        "label": label,
        "band": row.overall_band,
        "criteria": row.criteria_scores or {},
        "created_at": row.created_at,
        "has_pdf": bool(row.feedback_pdf_path),
        "assignment_part_id": row.assignment_part_id,
        "paid_by_id": row.paid_by_id,
    }


async def get_works_since(
    session: AsyncSession, user_id: int, since: datetime, until: datetime | None = None
) -> list[dict]:
    """A student's graded Writing+Speaking work from `since` on, newest first."""
    works = []
    for kind, model in (("w", WritingSubmission), ("s", SpeakingSubmission)):
        conditions = [model.user_id == user_id, model.overall_band.is_not(None), model.created_at >= since]
        if until is not None:
            conditions.append(model.created_at < until)
        rows = (await session.execute(select(model).where(*conditions))).scalars().all()
        works += [work_info(kind, row) for row in rows]
    return sorted(works, key=lambda w: w["created_at"], reverse=True)


def summarize_works(works: list[dict]) -> dict:
    """Counts, average bands per skill, the weakest criterion (key, average)
    and the last activity date of a list from get_works_since."""

    def average(items: list[dict]) -> float | None:
        return sum(w["band"] for w in items) / len(items) if items else None

    writing = [w for w in works if w["kind"] == "w"]
    speaking = [w for w in works if w["kind"] == "s"]
    criteria = criteria_averages(works)
    return {
        "total": len(works),
        "average": average(works),
        "writing": len(writing),
        "writing_avg": average(writing),
        "speaking": len(speaking),
        "speaking_avg": average(speaking),
        "criteria": criteria,
        "weakest": min(criteria.items(), key=lambda kv: kv[1]) if criteria else None,
        "last_active": max((w["created_at"] for w in works), default=None),
    }


def criteria_averages(works: list[dict]) -> dict[str, float]:
    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for work in works:
        for key, value in work["criteria"].items():
            if value is not None:
                sums[key] = sums.get(key, 0.0) + value
                counts[key] = counts.get(key, 0) + 1
    return {key: sums[key] / counts[key] for key in sums}


def activity_streak(works: list[dict]) -> int:
    """Consecutive days (Tashkent), ending today or yesterday, with at least one check."""
    days = {(w["created_at"] + _TASHKENT_OFFSET).date() for w in works}
    day = (datetime.now(timezone.utc) + _TASHKENT_OFFSET).date()
    if day not in days:
        day -= timedelta(days=1)
    streak = 0
    while day in days:
        streak += 1
        day -= timedelta(days=1)
    return streak


async def get_group_works(session: AsyncSession, group_id: int) -> list[tuple[User, datetime, list[dict]]]:
    """Every member with their work since joining."""
    result = []
    for user, joined_at in await get_group_members(session, group_id):
        result.append((user, joined_at, await get_works_since(session, user.id, joined_at)))
    return result


async def get_group_overview(session: AsyncSession, group_id: int) -> dict:
    members = await get_group_works(session, group_id)
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    per_student = [(user, summarize_works(works)) for user, _, works in members]
    all_works = [w for _, _, works in members for w in works]
    most_active = sorted((p for p in per_student if p[1]["total"]), key=lambda p: -p[1]["total"])[:3]
    return {
        "members": len(members),
        "active_week": sum(1 for _, s in per_student if s["last_active"] and s["last_active"] >= week_ago),
        "inactive": sum(1 for _, s in per_student if not s["total"]),
        "summary": summarize_works(all_works),
        "most_active": [(user.full_name, s["total"]) for user, s in most_active],
    }


async def get_group_leaderboard(session: AsyncSession, group_id: int) -> dict:
    """This week's most active students, current streaks and biggest band gains
    (the last 14 days against the 14 before, at least 2 works in each)."""
    members = await get_group_works(session, group_id)
    week_start = week_start_tashkent_as_utc()
    now = datetime.now(timezone.utc)
    week, streaks, improved = [], [], []
    for user, _, works in members:
        this_week = [w for w in works if w["created_at"] >= week_start]
        if this_week:
            week.append((user, len(this_week)))
        streak = activity_streak(works)
        if streak:
            streaks.append((user, streak))
        recent = [w["band"] for w in works if w["created_at"] >= now - timedelta(days=14)]
        before = [w["band"] for w in works if now - timedelta(days=28) <= w["created_at"] < now - timedelta(days=14)]
        if len(recent) >= 2 and len(before) >= 2:
            delta = sum(recent) / len(recent) - sum(before) / len(before)
            if delta > 0:
                improved.append((user, delta))
    return {
        "week": sorted(week, key=lambda p: -p[1]),
        "streaks": sorted(streaks, key=lambda p: -p[1]),
        "improved": sorted(improved, key=lambda p: -p[1]),
    }


async def get_work(session: AsyncSession, kind: str, submission_id: int):
    model = WritingSubmission if kind == "w" else SpeakingSubmission
    return await session.get(model, submission_id)


async def find_managed_membership(
    session: AsyncSession, manager_id: int, student_id: int
) -> GroupMembership | None:
    """The earliest membership of `student_id` in a group `manager_id` manages."""
    for group in await get_managed_groups(session, manager_id):
        membership = await get_membership(session, group.id, student_id)
        if membership is not None:
            return membership
    return None


async def get_visible_work(session: AsyncSession, manager_id: int, kind: str, submission_id: int):
    """A student's submission, if `manager_id` manages a group the student is
    in and the work was submitted after they joined it - else None."""
    work = await get_work(session, kind, submission_id)
    if work is None:
        return None
    membership = await find_managed_membership(session, manager_id, work.user_id)
    if membership is None or work.created_at < membership.joined_at:
        return None
    return work


async def get_member_report_pdf(
    session: AsyncSession, manager_id: int, kind: str, submission_id: int
) -> str | None:
    work = await get_visible_work(session, manager_id, kind, submission_id)
    return work.feedback_pdf_path if work is not None and work.feedback_pdf_path else None


# --- coins: gifts and sponsored checks ---


async def gift_coins(
    session: AsyncSession, *, group: Group, student_ids: list[int], amount: int
) -> list[User] | None:
    """Moves `amount` coins from the group's payer (teacher or center owner)
    to each student in `student_ids`, in one transaction - only if every one
    of them is in the group and the payer's balance covers them all. Returns
    the students who got them, or None if nothing was transferred."""
    if amount <= 0 or not student_ids:
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
    payer_id = await get_group_payer_id(session, group)
    # Row locks: two quick taps must not both spend the same balance.
    payer = (await session.execute(select(User).where(User.id == payer_id).with_for_update())).scalar_one_or_none()
    if payer is None or payer.coin_balance < amount * len(members):
        await session.rollback()
        return None
    students = list(
        (await session.execute(select(User).where(User.id.in_(members)).with_for_update())).scalars().all()
    )
    payer.coin_balance -= amount * len(students)
    for student in students:
        student.coin_balance += amount
    await session.commit()
    return students


async def count_sponsored_today(session: AsyncSession, user_id: int, payer_id: int) -> int:
    since = _today_start_tashkent_as_utc()
    total = 0
    for model in (WritingSubmission, SpeakingSubmission):
        total += (await session.execute(
            select(_checks(model)).select_from(model).where(
                model.user_id == user_id,
                model.paid_by_id == payer_id,
                model.created_at >= since,
                model.assignment_part_id.is_(None),
            )
        )).scalar() or 0
    return total


async def find_sponsor(
    session: AsyncSession, user_id: int, assignment_group_id: int | None = None, *, coins: int, checks: int = 1
) -> int | None:
    """The payer who covers this student's next check (costing `coins`, and
    counting `checks` against a daily limit - a full Speaking test is 3): a
    sponsored group they are in whose payer still has the coins and whose
    daily limit isn't used up (assignments of that group are never
    limited). None if nobody does."""
    for group, _ in await get_member_groups(session, user_id):
        if not group.sponsor_checks:
            continue
        payer_id = await get_group_payer_id(session, group)
        payer = await session.get(User, payer_id)
        if payer is None or payer.coin_balance < coins:
            continue
        if assignment_group_id == group.id or group.daily_limit is None:
            return payer_id
        if await count_sponsored_today(session, user_id, payer_id) + checks <= group.daily_limit:
            return payer_id
    return None


async def sponsored_spend(session: AsyncSession, payer_id: int, since: datetime) -> int:
    """Coins `payer_id` has spent on their students' checks since `since`."""
    total = 0
    for model in (WritingSubmission, SpeakingSubmission):
        total += (await session.execute(
            select(func.coalesce(func.sum(model.coins_spent), 0)).where(
                model.paid_by_id == payer_id, model.created_at >= since
            )
        )).scalar() or 0
    return total


# --- assignments ---


async def pick_practice_question(
    session: AsyncSession, module: PracticeModule, exclude_ids: list[int] | None = None
) -> PracticeQuestion | None:
    stmt = select(PracticeQuestion).where(PracticeQuestion.module == module)
    if exclude_ids:
        stmt = stmt.where(PracticeQuestion.id.not_in(exclude_ids))
    return (await session.execute(stmt.order_by(func.random()).limit(1))).scalar_one_or_none()


async def pick_full_test(session: AsyncSession) -> tuple[PracticeQuestion, PracticeQuestion, PracticeQuestion] | None:
    """A random Part 1 topic and a random Part 2 cue card with its own Part 3
    discussion questions - in the channel the Part 3 post comes right after
    its cue card (services/practice_import/speaking.py), so its source_ref is
    the card's plus one. None if the bank has no such pair yet."""
    part1 = await pick_practice_question(session, PracticeModule.speaking_part1)
    cards = (await session.execute(
        select(PracticeQuestion).where(PracticeQuestion.module == PracticeModule.speaking_part2)
    )).scalars().all()
    follow_ups = (await session.execute(
        select(PracticeQuestion).where(PracticeQuestion.module == PracticeModule.speaking_part3)
    )).scalars().all()
    by_ref = {(q.source, q.source_ref): q for q in follow_ups if q.source_ref and q.source_ref.isdigit()}
    pairs = [
        (card, by_ref[(card.source, str(int(card.source_ref) + 1))])
        for card in cards
        if card.source_ref and card.source_ref.isdigit() and (card.source, str(int(card.source_ref) + 1)) in by_ref
    ]
    if part1 is None or not pairs:
        return None
    card, follow_up = random.choice(pairs)
    return part1, card, follow_up


async def create_assignment(
    session: AsyncSession,
    *,
    group_id: int,
    kind: str,
    title: str,
    due_at: datetime,
    created_by: int,
    parts: list[dict],
) -> Assignment:
    """`parts`: dicts with module, prompt_text, image_path, practice_question_id."""
    assignment = Assignment(group_id=group_id, kind=kind, title=title, due_at=due_at, created_by=created_by)
    session.add(assignment)
    await session.flush()
    for position, part in enumerate(parts):
        session.add(AssignmentPart(assignment_id=assignment.id, position=position, **part))
    await session.commit()
    await session.refresh(assignment)
    return assignment


async def get_assignment(session: AsyncSession, assignment_id: int) -> Assignment | None:
    return await session.get(Assignment, assignment_id)


async def get_assignment_parts(session: AsyncSession, assignment_id: int) -> list[AssignmentPart]:
    stmt = select(AssignmentPart).where(AssignmentPart.assignment_id == assignment_id).order_by(AssignmentPart.position)
    return list((await session.execute(stmt)).scalars().all())


async def get_part(session: AsyncSession, part_id: int) -> tuple[AssignmentPart, Assignment] | None:
    part = await session.get(AssignmentPart, part_id)
    if part is None:
        return None
    assignment = await session.get(Assignment, part.assignment_id)
    return (part, assignment) if assignment is not None else None


async def delete_assignment(session: AsyncSession, assignment_id: int) -> None:
    part_ids = select(AssignmentPart.id).where(AssignmentPart.assignment_id == assignment_id)
    for model in (WritingSubmission, SpeakingSubmission):
        await session.execute(update(model).where(model.assignment_part_id.in_(part_ids)).values(assignment_part_id=None))
    await session.execute(delete(AssignmentPart).where(AssignmentPart.assignment_id == assignment_id))
    await session.execute(delete(Assignment).where(Assignment.id == assignment_id))
    await session.commit()


async def get_part_bands(session: AsyncSession, part_ids: list[int], user_ids: list[int] | None = None) -> dict:
    """{(user_id, part_id): (band, kind, submission_id)} - the latest answer to each part."""
    result: dict = {}
    if not part_ids:
        return result
    for kind, model in (("w", WritingSubmission), ("s", SpeakingSubmission)):
        stmt = select(model).where(model.assignment_part_id.in_(part_ids), model.overall_band.is_not(None))
        if user_ids is not None:
            stmt = stmt.where(model.user_id.in_(user_ids))
        for row in (await session.execute(stmt.order_by(model.created_at))).scalars().all():
            result[(row.user_id, row.assignment_part_id)] = (row.overall_band, kind, row.id)
    return result


def mock_overall(bands: dict[str, float | None]) -> dict:
    """Writing = (Task 1 + 2 x Task 2) / 3, Speaking = mean of its parts,
    overall = their mean - each rounded to the nearest 0.5 (a Writing+Speaking
    estimate, not a full four-skill IELTS score). `bands` maps module -> band."""

    def half(value: float) -> float:
        return round(value * 2) / 2

    w1, w2 = bands.get("writing_task1"), bands.get("writing_task2")
    speaking = [bands[m] for m in ("speaking_part1", "speaking_part2", "speaking_part3") if bands.get(m) is not None]
    writing = half((w1 + 2 * w2) / 3) if w1 is not None and w2 is not None else None
    speaking_band = half(sum(speaking) / len(speaking)) if len(speaking) == 3 else None
    overall = half((writing + speaking_band) / 2) if writing is not None and speaking_band is not None else None
    return {"writing": writing, "speaking": speaking_band, "overall": overall}


async def get_assignment_results(session: AsyncSession, assignment: Assignment) -> dict:
    """Per member (in joining order): their band for each part and how many parts are done."""
    parts = await get_assignment_parts(session, assignment.id)
    members = await get_group_members(session, assignment.group_id)
    bands = await get_part_bands(session, [p.id for p in parts], [u.id for u, _ in members])
    rows = []
    for user, _ in members:
        part_bands = [bands.get((user.id, p.id)) for p in parts]
        done = sum(1 for b in part_bands if b is not None)
        mock = (
            mock_overall({p.module: (b[0] if b else None) for p, b in zip(parts, part_bands)})
            if assignment.kind == "mock" else None
        )
        rows.append({"user": user, "bands": part_bands, "done": done, "mock": mock})
    return {"parts": parts, "rows": rows, "complete": sum(1 for r in rows if r["done"] == len(parts))}


async def list_group_assignments(session: AsyncSession, group_id: int, limit: int = 20) -> list[dict]:
    stmt = select(Assignment).where(Assignment.group_id == group_id).order_by(Assignment.created_at.desc()).limit(limit)
    result = []
    members = await get_group_member_count(session, group_id)
    for assignment in (await session.execute(stmt)).scalars().all():
        results = await get_assignment_results(session, assignment)
        result.append({"assignment": assignment, "complete": results["complete"], "members": members})
    return result


async def get_student_assignments(session: AsyncSession, user_id: int, closed_days: int = 7) -> list[dict]:
    """Open assignments of the student's groups, plus those closed in the last
    `closed_days` days (latest deadline first), each with its parts and the
    bands of the parts the student has done."""
    since = datetime.now(timezone.utc) - timedelta(days=closed_days)
    result = []
    for group, _ in await get_member_groups(session, user_id):
        stmt = select(Assignment).where(Assignment.group_id == group.id, Assignment.due_at >= since)
        for assignment in (await session.execute(stmt)).scalars().all():
            parts = await get_assignment_parts(session, assignment.id)
            bands = await get_part_bands(session, [p.id for p in parts], [user_id])
            result.append({
                "assignment": assignment,
                "group": group,
                "parts": parts,
                "done": {p.id: bands[(user_id, p.id)][0] for p in parts if (user_id, p.id) in bands},
            })
    return sorted(result, key=lambda a: a["assignment"].due_at, reverse=True)


async def take_due_reminders(session: AsyncSession, within: timedelta) -> list[dict]:
    """Assignments due within `within` that haven't been reminded yet - marked
    reminded here (before sending, so a restart never reminds twice) - with
    the members who haven't finished them."""
    now = datetime.now(timezone.utc)
    stmt = select(Assignment).where(
        Assignment.reminded.is_(False), Assignment.due_at > now, Assignment.due_at <= now + within
    )
    due = list((await session.execute(stmt)).scalars().all())
    result = []
    for assignment in due:
        assignment.reminded = True
        results = await get_assignment_results(session, assignment)
        pending = [r["user"] for r in results["rows"] if r["done"] < len(results["parts"])]
        group = await session.get(Group, assignment.group_id)
        result.append({"assignment": assignment, "group": group, "parts": results["parts"], "pending": pending})
    await session.commit()
    return result


# --- teacher comments ---


async def add_comment(
    session: AsyncSession, *, kind: str, submission_id: int, teacher_id: int, text: str, band: float | None
) -> SubmissionComment:
    comment = SubmissionComment(kind=kind, submission_id=submission_id, teacher_id=teacher_id, text=text, band=band)
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    return comment


async def get_comments(session: AsyncSession, kind: str, submission_id: int) -> list[SubmissionComment]:
    stmt = (
        select(SubmissionComment)
        .where(SubmissionComment.kind == kind, SubmissionComment.submission_id == submission_id)
        .order_by(SubmissionComment.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_comments_for_works(session: AsyncSession, works: list[dict]) -> dict:
    """{(kind, submission_id): [comments]} for a list from get_works_since."""
    result: dict = {}
    for kind in ("w", "s"):
        ids = [w["id"] for w in works if w["kind"] == kind]
        if not ids:
            continue
        stmt = (
            select(SubmissionComment)
            .where(SubmissionComment.kind == kind, SubmissionComment.submission_id.in_(ids))
            .order_by(SubmissionComment.created_at)
        )
        for comment in (await session.execute(stmt)).scalars().all():
            result.setdefault((kind, comment.submission_id), []).append(comment)
    return result


# --- weekly digest ---


async def take_digest_groups(session: AsyncSession, week: str) -> list[Group]:
    """Groups with at least one member whose digest for `week` hasn't gone out -
    marked here (before sending, so a restart never sends twice)."""
    stmt = select(Group).where(or_(Group.digest_week.is_(None), Group.digest_week != week))
    groups = []
    for group in (await session.execute(stmt)).scalars().all():
        group.digest_week = week
        if await get_group_member_count(session, group.id):
            groups.append(group)
    await session.commit()
    return groups


async def list_teachers_with_stats(session: AsyncSession) -> list[dict]:
    """Every teacher / group owner for the web panel, with their groups,
    students, center and the last 30 days of their students' checks."""
    month_ago = datetime.now(timezone.utc) - timedelta(days=30)
    owners = set((await session.execute(select(Group.teacher_id))).scalars().all())
    teachers = (await session.execute(
        select(User).where(or_(User.role == UserRole.teacher, User.id.in_(owners))).order_by(User.created_at)
    )).scalars().all()
    result = []
    for teacher in teachers:
        groups = (await session.execute(select(Group).where(Group.teacher_id == teacher.id))).scalars().all()
        students = 0
        checks = 0
        for group in groups:
            for user, joined_at in await get_group_members(session, group.id):
                students += 1
                checks += len(await get_works_since(session, user.id, max(joined_at, month_ago)))
        center = await get_user_center(session, teacher.id)
        result.append({
            "user": teacher,
            "groups": len(groups),
            "students": students,
            "checks_30d": checks,
            "sponsored_30d": await sponsored_spend(session, teacher.id, month_ago),
            "center": center,
            "is_owner": center is not None and center.owner_id == teacher.id,
        })
    return result


async def list_centers_with_stats(session: AsyncSession) -> list[dict]:
    month_ago = datetime.now(timezone.utc) - timedelta(days=30)
    result = []
    for center in (await session.execute(select(Center).order_by(Center.created_at))).scalars().all():
        owner = await session.get(User, center.owner_id)
        teachers = await list_center_teachers(session, center.id)
        groups = (await session.execute(select(Group).where(Group.center_id == center.id))).scalars().all()
        students = sum([await get_group_member_count(session, g.id) for g in groups])
        result.append({
            "center": center,
            "owner": owner,
            "teachers": len(teachers),
            "groups": len(groups),
            "students": students,
            "balance": owner.coin_balance if owner else 0,
            "sponsored_30d": await sponsored_spend(session, center.owner_id, month_ago),
        })
    return result


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
    # the buyer comes along for the admin panel's Foydalanuvchi column (@username)
    stmt = (
        select(PaymentRequest).options(selectinload(PaymentRequest.user))
        .order_by(PaymentRequest.created_at.desc()).limit(limit)
    )
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


# --- Admins' group (bot/utils/crm_events.py) --------------------------------------------------


async def count_users(session: AsyncSession, start: datetime | None = None, end: datetime | None = None) -> int:
    stmt = select(func.count()).select_from(User)
    if start is not None:
        stmt = stmt.where(User.created_at >= start)
    if end is not None:
        stmt = stmt.where(User.created_at < end)
    return (await session.execute(stmt)).scalar() or 0


async def checks_before(session: AsyncSession, user_id: int, created_at: datetime) -> int:
    """How many checks (a full Speaking test: FULL_TEST_CHECKS) the user had
    before this moment - an old check within the first free ones was free."""
    w = await session.execute(
        select(func.count()).select_from(WritingSubmission)
        .where(WritingSubmission.user_id == user_id, WritingSubmission.created_at < created_at)
    )
    s = await session.execute(
        select(_speaking_checks()).select_from(SpeakingSubmission)
        .where(SpeakingSubmission.user_id == user_id, SpeakingSubmission.created_at < created_at)
    )
    return (w.scalar() or 0) + (s.scalar() or 0)


async def user_activity(session: AsyncSession) -> dict[int, dict]:
    """{user id: {"writing", "speaking", "paid" (so'm approved)}} for everyone with any."""
    out: dict[int, dict] = {}

    def row(user_id: int) -> dict:
        return out.setdefault(user_id, {"writing": 0, "speaking": 0, "paid": 0})

    for user_id, n in (await session.execute(
        select(WritingSubmission.user_id, func.count()).group_by(WritingSubmission.user_id)
    )).all():
        row(user_id)["writing"] = n
    for user_id, n in (await session.execute(
        select(SpeakingSubmission.user_id, func.count()).group_by(SpeakingSubmission.user_id)
    )).all():
        row(user_id)["speaking"] = n
    for user_id, amount in (await session.execute(
        select(PaymentRequest.user_id, func.sum(PaymentRequest.amount))
        .where(PaymentRequest.status == PaymentStatus.approved).group_by(PaymentRequest.user_id)
    )).all():
        row(user_id)["paid"] = amount or 0
    return out


async def users_until(session: AsyncSession, until: datetime) -> list[User]:
    return list((await session.execute(
        select(User).where(User.created_at <= until).order_by(User.created_at)
    )).scalars().all())


async def payments_until(session: AsyncSession, until: datetime) -> list[PaymentRequest]:
    return list((await session.execute(
        select(PaymentRequest).where(PaymentRequest.created_at <= until).order_by(PaymentRequest.created_at)
    )).scalars().all())


async def submission_ids_until(session: AsyncSession, until: datetime) -> tuple[list[int], list[int]]:
    w = (await session.execute(
        select(WritingSubmission.id).where(WritingSubmission.created_at <= until).order_by(WritingSubmission.created_at)
    )).scalars().all()
    s = (await session.execute(
        select(SpeakingSubmission.id).where(SpeakingSubmission.created_at <= until).order_by(SpeakingSubmission.created_at)
    )).scalars().all()
    return list(w), list(s)


async def checks_between(
    session: AsyncSession, start: datetime, end: datetime
) -> tuple[list[WritingSubmission], list[SpeakingSubmission]]:
    w = (await session.execute(
        select(WritingSubmission).where(WritingSubmission.created_at >= start, WritingSubmission.created_at < end)
    )).scalars().all()
    s = (await session.execute(
        select(SpeakingSubmission).where(SpeakingSubmission.created_at >= start, SpeakingSubmission.created_at < end)
    )).scalars().all()
    return list(w), list(s)


async def payments_decided_between(session: AsyncSession, start: datetime, end: datetime) -> list[PaymentRequest]:
    return list((await session.execute(
        select(PaymentRequest).where(PaymentRequest.reviewed_at >= start, PaymentRequest.reviewed_at < end)
    )).scalars().all())


async def count_pending_payments(session: AsyncSession) -> int:
    """Receipts sent and still waiting for an admin."""
    return (await session.execute(
        select(func.count()).select_from(PaymentRequest).where(
            PaymentRequest.status == PaymentStatus.pending, PaymentRequest.receipt_file_id.is_not(None)
        )
    )).scalar() or 0
