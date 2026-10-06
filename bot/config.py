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
    telegram_bot_username: str = "writespeak_bot"

    # --- OpenAI ---
    # Verified against a live key on 2026-08-28: gpt-4o-audio-preview has been
    # fully retired (404 model_not_found); gpt-5 supports structured outputs
    # and vision; gpt-audio-1.5 is audio-capable but does NOT support
    # structured `response_format` (see services/ai/speaking_grader.py for
    # how that's worked around). Re-verify with a live key before changing
    # these again - this is the fastest-moving part of OpenAI's API surface.
    openai_api_key: str
    openai_model_grading: str = "gpt-5"
    openai_model_vision: str = "gpt-5"
    openai_model_audio: str = "gpt-audio-1.5"
    openai_model_transcribe: str = "whisper-1"
    openai_embedding_model: str = "text-embedding-3-small"

    # --- Database ---
    database_url: str
    database_url_sync: str = ""

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"

    # --- Storage ---
    file_storage_path: str = "./data/storage"
    books_path: str = "./data/books"

    # --- Limits & payments ---
    # Free Writing+Speaking checks per user per rolling 24h (combined pool).
    free_daily_limit: int = 2
    # Price in so'm for one check beyond the free daily quota.
    price_per_check: int = 5000
    # Card shown to students for the manual top-up flow (bot/handlers/payments.py).
    payment_card_number: str = "0000 0000 0000 0000"
    payment_card_holder: str = "F.I.Sh."
    # Monthly unlimited-checks subscription price, in so'm.
    subscription_price_monthly: int = 99000

    # --- Web admin panel (webapp/main.py) ---
    # Single shared login, not tied to Telegram accounts - see webapp/main.py's
    # module docstring. Served as https://admin.writespeak.uz via the host's
    # nginx (TLS from certbot) since 2026-10-06; the container port is only
    # published on 127.0.0.1 (docker-compose.yml).
    web_admin_username: str = "admin"
    web_admin_password: str = "change-me"
    web_session_secret: str = "change-me-too"
    # Session cookie only sent over HTTPS - true in production, false for a
    # local http:// run (otherwise login can't stick).
    web_cookie_secure: bool = False

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


settings = Settings()
