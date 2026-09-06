"""Small, focused query helpers used by handlers/services.

Kept deliberately thin (no repository abstraction) — each function takes an
already-open AsyncSession so callers control the transaction boundary via
`db.database.get_session()`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
from services.ai.schemas import SpeakingGradingResult, VocabularyItem, WritingGradingResult

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
    result: WritingGradingResult,
    book_sample_ref: int | None,
    feedback_pdf_path: str,
    practice_question_id: int | None = None,
    spend_credit: bool = False,
) -> WritingSubmission:
    submission = WritingSubmission(
        user_id=user_id,
        task_type=task_type,
        prompt_text=prompt_text,
        prompt_source=prompt_source,
        answer_text=answer_text,
        answer_source=answer_source,
        original_file_paths=original_file_paths,
        overall_band=result.overall_band,
        criteria_scores={
            "TA": result.task_achievement_or_response,
            "CC": result.coherence_and_cohesion,
            "LR": result.lexical_resource,
            "GRA": result.grammatical_range_and_accuracy,
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


async def get_daily_submission_count(session: AsyncSession, user_id: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    w = await session.execute(
        select(func.count())
        .select_from(WritingSubmission)
        .where(WritingSubmission.user_id == user_id, WritingSubmission.created_at >= since)
    )
    s = await session.execute(
        select(func.count())
        .select_from(SpeakingSubmission)
        .where(SpeakingSubmission.user_id == user_id, SpeakingSubmission.created_at >= since)
    )
    return (w.scalar() or 0) + (s.scalar() or 0)


async def get_total_submission_count(session: AsyncSession, user_id: int) -> int:
    """Lifetime count, no time window - used to detect a user's very first
    completed check (see reward_referrer_if_eligible)."""
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


async def add_vocabulary_entries(
    session: AsyncSession, user_id: int, items: list[VocabularyItem], source: str
) -> None:
    for item in items:
        session.add(
            VocabularyEntry(
                user_id=user_id,
                word_or_phrase=item.word_or_phrase,
                meaning=item.meaning,
                example_sentence=item.example_sentence,
                source=source,
            )
        )
    await session.commit()


async def get_vocabulary_for_review(
    session: AsyncSession, user_id: int, limit: int = 10
) -> list[VocabularyEntry]:
    stmt = (
        select(VocabularyEntry)
        .where(VocabularyEntry.user_id == user_id, VocabularyEntry.is_learned.is_(False))
        .order_by(func.random())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_vocabulary_counts(session: AsyncSession, user_id: int) -> tuple[int, int]:
    """Returns (total, learned)."""
    total = await session.execute(
        select(func.count()).select_from(VocabularyEntry).where(VocabularyEntry.user_id == user_id)
    )
    learned = await session.execute(
        select(func.count())
        .select_from(VocabularyEntry)
        .where(VocabularyEntry.user_id == user_id, VocabularyEntry.is_learned.is_(True))
    )
    return (total.scalar() or 0), (learned.scalar() or 0)


async def mark_vocabulary_learned(session: AsyncSession, entry_id: int, user_id: int) -> None:
    entry = await session.get(VocabularyEntry, entry_id)
    if entry is not None and entry.user_id == user_id:
        entry.is_learned = True
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
        .order_by(PracticeQuestion.order_index)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_practice_question(session: AsyncSession, question_id: int) -> PracticeQuestion | None:
    return await session.get(PracticeQuestion, question_id)


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


async def get_group_for_member(session: AsyncSession, user_id: int) -> Group | None:
    """The most recently joined group this user is a student in, if any."""
    stmt = (
        select(Group)
        .join(GroupMembership, GroupMembership.group_id == Group.id)
        .where(GroupMembership.user_id == user_id)
        .order_by(GroupMembership.joined_at.desc())
    )
    return (await session.execute(stmt)).scalars().first()


async def join_group(session: AsyncSession, group_id: int, user_id: int) -> None:
    """Idempotent - tapping the same invite link twice is a no-op, not an
    error, since the unique constraint would otherwise raise."""
    existing = await session.execute(
        select(GroupMembership).where(
            GroupMembership.group_id == group_id, GroupMembership.user_id == user_id
        )
    )
    if existing.scalar_one_or_none() is not None:
        return
    session.add(GroupMembership(group_id=group_id, user_id=user_id))
    await session.commit()


async def get_group_member_count(session: AsyncSession, group_id: int) -> int:
    result = await session.execute(
        select(func.count()).select_from(GroupMembership).where(GroupMembership.group_id == group_id)
    )
    return result.scalar() or 0


async def get_group_members(session: AsyncSession, group_id: int, limit: int = 30) -> list[User]:
    stmt = (
        select(User)
        .join(GroupMembership, GroupMembership.user_id == User.id)
        .where(GroupMembership.group_id == group_id)
        .order_by(GroupMembership.joined_at)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_member_activity(session: AsyncSession, user_id: int) -> dict:
    """Summary used in a teacher's group view: total checks, average band,
    and the most recent submission date, across Writing+Speaking."""
    w_rows = (
        await session.execute(
            select(WritingSubmission.overall_band, WritingSubmission.created_at).where(
                WritingSubmission.user_id == user_id, WritingSubmission.overall_band.is_not(None)
            )
        )
    ).all()
    s_rows = (
        await session.execute(
            select(SpeakingSubmission.overall_band, SpeakingSubmission.created_at).where(
                SpeakingSubmission.user_id == user_id, SpeakingSubmission.overall_band.is_not(None)
            )
        )
    ).all()
    all_rows = [*w_rows, *s_rows]
    return {
        "total": len(all_rows),
        "avg_band": (sum(r[0] for r in all_rows) / len(all_rows)) if all_rows else None,
        "last_active": max((r[1] for r in all_rows), default=None),
    }


async def gift_credits(session: AsyncSession, *, teacher_id: int, student_id: int, amount: int) -> bool:
    """Transfers `amount` credits from the teacher's own balance to a
    student's - only if the teacher actually owns the group that student
    belongs to, and has enough balance. Returns whether the transfer
    happened, so the handler can tell the teacher why it didn't if not."""
    group = await get_owned_group(session, teacher_id)
    if group is None:
        return False
    membership = await session.execute(
        select(GroupMembership).where(
            GroupMembership.group_id == group.id, GroupMembership.user_id == student_id
        )
    )
    if membership.scalar_one_or_none() is None:
        return False

    teacher = await session.get(User, teacher_id)
    student = await session.get(User, student_id)
    if teacher is None or student is None or teacher.credit_balance < amount:
        return False

    teacher.credit_balance -= amount
    student.credit_balance += amount
    await session.commit()
    return True


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
