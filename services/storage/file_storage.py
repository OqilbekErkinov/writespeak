"""Local-disk storage for user uploads and generated feedback reports.

Files live under `settings.file_storage_path` (a Docker volume — see
docker-compose.yml), organized per-user so a future migration to an
S3-compatible backend only touches this module.
"""
from __future__ import annotations

import io
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

from bot.config import settings

logger = logging.getLogger(__name__)

MAX_IMAGE_SIDE = 2560


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


def save_practice_image(content: bytes) -> str:
    """Saves a practice question's chart/diagram (web admin panel upload),
    normalized to something Telegram's sendPhoto and the vision model both
    accept: PNG for line-art sources (keeps chart lines crisp), JPEG
    otherwise, at most MAX_IMAGE_SIDE px. Raises ValueError for non-images."""
    try:
        image = Image.open(io.BytesIO(content))
        source_format = image.format
        image = ImageOps.exif_transpose(image)
    except Exception as e:
        raise ValueError("not an image") from e

    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")

    keep_png = source_format in ("PNG", "GIF", "BMP")
    d = settings.storage_dir / "practice"
    d.mkdir(parents=True, exist_ok=True)
    path = d / _timestamped_name(".png" if keep_png else ".jpg")
    if keep_png:
        image.save(path, format="PNG", optimize=True)
    else:
        image.save(path, format="JPEG", quality=92)
    return str(path)


def delete_file(path: str | None) -> None:
    """Best-effort cleanup - a leftover file is harmless, a crash after the
    DB change already committed is not."""
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete %s", path, exc_info=True)
