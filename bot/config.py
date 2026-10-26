"""Central application configuration, loaded from environment variables / .env."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Telegram ---
    telegram_bot_token: str
    admin_telegram_ids: str  # comma-separated, parsed via admin_ids property
    # No leading @ - used to build referral links (bot/handlers/referral.py).
    telegram_bot_username: str = "bandzilla_bot"
    # Signs group/center invite links (bot/utils/group_invite.py); empty = the
    # bot token. Since the move to @bandzilla_bot (2026-10-17) it is the old
    # bot's token, so the links and QR codes handed out before keep working.
    invite_secret: str = ""
    # The previous bot (@writespeak_bot): bot/moved.py answers everyone there
    # with a link to this one. Empty = that service idles.
    moved_bot_token: str = ""

    # --- OpenAI ---
    # Verified against a live key on 2026-08-28: gpt-4o-audio-preview has been
    # fully retired (404 model_not_found); gpt-5 supports structured outputs
    # and vision; gpt-audio-1.5 is audio-capable but does NOT support
    # structured `response_format` (see services/ai/speaking_grader.py for
    # how that's worked around). Re-verify with a live key before changing
    # these again - this is the fastest-moving part of OpenAI's API surface.
    openai_api_key: str
    openai_model_grading: str = "gpt-5"
    # Book-style "Namuna" samples, writer style profiles and book-library
    # classification: a cheaper model is enough there (the grade itself stays
    # on openai_model_grading).
    openai_model_sample: str = "gpt-5-mini"
    openai_model_vision: str = "gpt-5"
    # Reasoning effort for OCR: transcribing a photo needs little reasoning, and
    # reasoning tokens are billed as output.
    openai_ocr_reasoning_effort: str = "low"
    # Reasoning effort for Writing grading (feedback, missed-errors pass,
    # model-answer self-check). Measured 2026-10-13 against the approved
    # report's 25 mistakes: "low" found 24/25 with the same bands as the
    # default, at about half the cost and time.
    openai_grading_reasoning_effort: str = "low"
    # Reasoning effort for reading a Task 1 chart into its data sheet - once per
    # check, and every other call uses its figures (services/ai/visual_reading.py).
    # "low" misread 3D bars by 2-5 points and ranked equal bars (report #62).
    openai_chart_reasoning_effort: str = "medium"
    # The Writing mistake filter (2026-10-26, the last prompt's A1): "medium" -
    # at "low" it kept correct English ("a renewed interest", "Malala Fund").
    openai_verify_reasoning_effort: str = "medium"
    # The Writing scoring runs: "low" until the low-vs-medium B4 experiment is
    # decided (academic lead + developer).
    openai_scoring_reasoning_effort: str = "low"
    # The grading calls (gpt-5: scores, feedback, missed-errors pass, checks) run
    # on OpenAI's Flex tier - half the token price, sometimes slower - and fall
    # back to the default tier when Flex is busy or takes longer than
    # openai_flex_timeout seconds (services/ai/openai_client.py). "" = default tier.
    openai_service_tier: str = "flex"
    openai_flex_timeout: float = 240.0
    openai_model_audio: str = "gpt-audio-1.5"
    openai_model_transcribe: str = "whisper-1"
    openai_embedding_model: str = "text-embedding-3-small"
    # Speaking model answers sent as voice messages (services/ai/tts.py).
    # Empty = no voice messages.
    openai_model_tts: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "coral"

    # --- Azure AI Speech: pronunciation assessment (services/ai/pronunciation.py) ---
    # Both empty = no assessment; the Speaking report then hides "Words to
    # practise" and "Intonation" and marks Pronunciation "(estimated)".
    azure_speech_key: str = ""
    azure_speech_region: str = ""

    # --- Speaking model answers (services/ai/speaking_answers.py) ---
    # True = a session question that matches a book question exactly is answered
    # with the book's own answer. Off since 2026-10-14: book content isn't
    # reproduced; books only lend an author's style. Turn on only for sources
    # you have permission to show.
    allow_book_answers_verbatim: bool = False

    # --- Database ---
    database_url: str
    database_url_sync: str = ""

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Storage ---
    file_storage_path: str = "./data/storage"
    books_path: str = "./data/books"

    # --- Coins & payments (bot/utils/coins.py: what each check and pack costs) ---
    # Coins every new user gets once, on signup.
    welcome_coins: int = 8
    # Coins the inviter and the invited user each get on the invited user's
    # first approved purchase.
    referral_coins: int = 5
    # So'm one coin is worth at the smallest pack's price - for revenue
    # estimates and "= N so'm" hints.
    coin_price: int = 1000
    # Card shown to students for the manual top-up flow (bot/handlers/payments.py).
    payment_card_number: str = "0000 0000 0000 0000"
    payment_card_holder: str = "F.I.Sh."
    # Test window - see the checks_only_for_ids property. Empty = off.
    checks_only_for: str = ""
    # So'm per US dollar for the admins' group's cost figures (bot/utils/crm.py)
    # when the Central Bank's daily rate can't be fetched.
    usd_uzs_rate: float = 11800.0

    # --- Vocabulary bank (bot/handlers/vocabulary.py) ---
    # Tashkent hour (0-23) of the daily "words to review" reminder
    # (bot/utils/vocab_reminders.py); -1 switches the reminders off.
    vocab_reminder_hour: int = 20

    # --- Web admin panel (webapp/main.py) ---
    # Single shared login, not tied to Telegram accounts - see webapp/main.py's
    # module docstring. Served as https://admin.bandzilla.uz via the host's
    # nginx (TLS from certbot) since 2026-10-06; the container port is only
    # published on 127.0.0.1 (docker-compose.yml).
    web_admin_username: str = "admin"
    web_admin_password: str = "change-me"
    web_session_secret: str = "change-me-too"
    # Session cookie only sent over HTTPS - true in production, false for a
    # local http:// run (otherwise login can't stick).
    web_cookie_secure: bool = False
    # Teachers' web cabinet (webapp/cabinet.py), e.g. https://bandzilla.uz/cabinet
    # - the bot's "Web-kabinet" button signs them in there. Empty = button hidden.
    cabinet_base_url: str = ""

    @property
    def checks_only_for_ids(self) -> set[int]:
        """A test window (CHECKS_ONLY_FOR, comma-separated Telegram IDs): while
        set, only these users and the admins can start checks - free of charge -
        and everyone else's coins and subscriptions are kept but
        paused (bot/utils/quota.py). Empty = normal operation."""
        return {int(x.strip()) for x in self.checks_only_for.split(",") if x.strip()}

    @property
    def admin_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_telegram_ids.split(",") if x.strip()}

    @property
    def storage_dir(self) -> Path:
        p = Path(self.file_storage_path)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def books_dir(self) -> Path:
        p = Path(self.books_path)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def sample_library_dir(self) -> Path:
        """The model-answer library (index.json, embeddings.json, styles/),
        next to the books so it sits on the same mounted volume."""
        p = Path(self.books_path).parent / "sample_library"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def calibration_dir(self) -> Path:
        """The Writing scorer's anchors and Task 1 overview patterns
        (services/ai/writing_anchors.py) and the calibration data behind
        them - book text, so on the data volume, never in the repo."""
        return Path(self.books_path).parent / "calibration"


settings = Settings()
