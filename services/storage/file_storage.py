"""Local-disk storage for user uploads and generated feedback reports.

Files live under `settings.file_storage_path` (a Docker volume — see
docker-compose.yml), organized per-user so a future migration to an
S3-compatible backend only touches this module.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from bot.config import settings


def _user_dir(user_id: int, subfolder: str) -> Path:
    d = settings.storage_dir / str(user_id) / subfolder
    d.mkdir(parents=True, exist_ok=True)
    return d


def _timestamped_name(extension: str) -> str:
    return f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{extension}"


def save_upload(user_id: int, content: bytes, extension: str) -> str:
    """Saves a raw user-uploaded file (prompt/answer image, pdf, docx, audio)."""
    path = _user_dir(user_id, "uploads") / _timestamped_name(extension)
    path.write_bytes(content)
    return str(path)


def save_report(user_id: int, content: bytes, extension: str = ".pdf") -> str:
    """Saves a generated feedback report (PDF/DOCX)."""
    path = _user_dir(user_id, "reports") / _timestamped_name(extension)
    path.write_bytes(content)
    return str(path)


def read_file(path: str) -> bytes:
    return Path(path).read_bytes()
