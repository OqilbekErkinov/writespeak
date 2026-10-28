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


def save_practice_image(content: bytes, folder: str = "practice") -> str:
    """Saves a practice question's chart/diagram (web admin panel upload),
    normalized to something Telegram's sendPhoto and the vision model both
    accept: PNG for line-art sources (keeps chart lines crisp), JPEG
    otherwise, at most MAX_IMAGE_SIDE px. Raises ValueError for non-images.
    `folder`: "chart_keys" for a chart key's own copy (services/ai/chart_keys.py)."""
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
    d = settings.storage_dir / folder
    d.mkdir(parents=True, exist_ok=True)
    path = d / _timestamped_name(".png" if keep_png else ".jpg")
    if keep_png:
        image.save(path, format="PNG", optimize=True)
    else:
        image.save(path, format="JPEG", quality=92)
    return str(path)


class BadImage(ValueError):
    """An upload that can't go to the vision model; `reason` is "not_image"
    (corrupt, truncated, not an image, or a format we can't open, e.g. HEIC)
    or "too_small" (a thumbnail whose labels can't be read)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# What the vision model gets (OpenAI's "high" detail fits an image into
# 2048x2048 itself, so nothing it would see is lost by doing it first), and
# the biggest file kept as it is: every grading call carries the chart again,
# base64-encoded (a third bigger).
VISION_MAX_SIDE = 2048
VISION_MAX_BYTES = 6 * 1024 * 1024
MIN_IMAGE_SIDE = 300  # the longer side: below it a chart's labels can't be read
_VISION_FORMATS = {"JPEG": ("image/jpeg", ".jpg"), "PNG": ("image/png", ".png"), "WEBP": ("image/webp", ".webp")}


def prepare_vision_image(content: bytes) -> tuple[bytes, str, str]:
    """(bytes, mime, extension) of an uploaded image the vision model accepts,
    judged by its bytes, never by the name or type the sender's app gave it.
    A JPEG/PNG/WebP that is already fine is kept byte for byte (a chart's
    small labels and figures stay as sharp as they came); anything else is
    turned upright (camera EXIF), flattened onto white, shrunk to
    VISION_MAX_SIDE (never enlarged) and saved as PNG - lossless, for charts
    and screenshots - or as JPEG for photos. Raises BadImage."""
    try:
        image = Image.open(io.BytesIO(content))
        image.load()  # a truncated or corrupt file fails here, not at OpenAI
    except Exception as e:
        raise BadImage("not_image") from e
    if max(image.size) < MIN_IMAGE_SIDE:
        raise BadImage("too_small")

    fmt = image.format
    orientation = image.getexif().get(0x0112, 1)
    has_alpha = image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info)
    if (fmt in _VISION_FORMATS and image.mode in ("RGB", "L", "P", "RGBA", "LA") and not has_alpha
            and orientation in (0, 1) and max(image.size) <= VISION_MAX_SIDE and len(content) <= VISION_MAX_BYTES
            and not getattr(image, "is_animated", False)):
        return content, *_VISION_FORMATS[fmt]

    image = ImageOps.exif_transpose(image)
    image.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE), Image.LANCZOS)
    if image.mode in ("RGBA", "LA", "PA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    if fmt not in ("JPEG", "MPO"):
        buf = io.BytesIO()
        image.save(buf, format="PNG", optimize=True)
        if buf.tell() <= VISION_MAX_BYTES:
            return buf.getvalue(), "image/png", ".png"
    for quality in (92, 85, 75, 60):  # the first that fits; a real photo fits at 92
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=quality, subsampling=0)  # 4:4:4 keeps small text crisp
        if buf.tell() <= VISION_MAX_BYTES:
            break
    return buf.getvalue(), "image/jpeg", ".jpg"


def image_mime(content: bytes, path: str = "") -> str:
    """The MIME type of stored image bytes, from their first bytes - a file's
    name can say otherwise (an older upload, a practice image)."""
    head = content[:12]
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    suffix = Path(path).suffix.lower()
    return {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(suffix, "image/jpeg")


def delete_file(path: str | None) -> None:
    """Best-effort cleanup - a leftover file is harmless, a crash after the
    DB change already committed is not."""
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete %s", path, exc_info=True)
