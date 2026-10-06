"""Audio -> text via OpenAI Whisper, plus a shared mp3-transcoding helper used
by both this module and speaking_grader.py's audio-input grading call.

Telegram voice messages arrive as .oga/.ogg (Opus codec), which neither the
Whisper transcription API nor the chat-completions audio-input format accept
directly — everything is normalized to .mp3 via ffmpeg first.
"""
from __future__ import annotations

import asyncio
import io
import tempfile
from pathlib import Path

from bot.config import settings
from services.ai.openai_client import client

async def ensure_mp3(audio_bytes: bytes, filename: str) -> bytes:
    """Returns audio_bytes transcoded to .mp3 if needed, unchanged otherwise.

    Whisper natively accepts several formats (m4a, wav, webm, ...), but the
    chat-completions audio-input format only documents wav/mp3, so everything
    is normalized to .mp3 here for one predictable format used by both."""
    suffix = Path(filename).suffix.lower() or ".ogg"
    if suffix == ".mp3":
        return audio_bytes
    return await _transcode_to_mp3(audio_bytes, suffix)


async def _transcode_to_mp3(audio_bytes: bytes, suffix: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{suffix}"
        dst = Path(tmp) / "output.mp3"
        src.write_bytes(audio_bytes)

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", str(src), str(dst),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode != 0 or not dst.exists():
            raise RuntimeError("ffmpeg audio transcoding failed")
        return dst.read_bytes()


async def concat_mp3(clips: list[bytes]) -> bytes:
    """Joins several mp3 clips into one, in order - a multi-question Speaking
    set (bot/handlers/speaking.py) is graded as one performance, like the
    real exam. Re-encoded mono 64 kbps to keep the audio-grading request
    small; ffmpeg's concat filter reconciles differing sample rates/layouts."""
    if len(clips) == 1:
        return clips[0]
    with tempfile.TemporaryDirectory() as tmp:
        inputs: list[str] = []
        for i, clip in enumerate(clips):
            src = Path(tmp) / f"clip{i}.mp3"
            src.write_bytes(clip)
            inputs += ["-i", str(src)]
        dst = Path(tmp) / "joined.mp3"
        streams = "".join(f"[{i}:a]" for i in range(len(clips)))

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", *inputs,
            "-filter_complex", f"{streams}concat=n={len(clips)}:v=0:a=1[out]",
            "-map", "[out]", "-ac", "1", "-b:a", "64k", str(dst),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        if proc.returncode != 0 or not dst.exists():
            raise RuntimeError("ffmpeg audio concatenation failed")
        return dst.read_bytes()


async def transcribe_mp3(mp3_bytes: bytes) -> str:
    """Returns the plain-text transcript of an mp3 buffer (for display in the
    PDF report - the audio-input grading call assesses the raw audio itself)."""
    response = await client.audio.transcriptions.create(
        model=settings.openai_model_transcribe,
        file=("audio.mp3", io.BytesIO(mp3_bytes)),
    )
    return response.text.strip()
