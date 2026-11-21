"""The panel's broadcast as a post (the owner, 2026-11-22): a picture or a video with the text, and "✍️ Writing /
🎙 Speaking" buttons under it. Offline: a fake Bot API session, a throwaway Postgres + Redis (run_test.sh).
"""
import asyncio
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from bot.config import settings  # noqa: E402

settings.web_2fa = False

fails: list[str] = []


def check(name: str, ok, detail="") -> None:
    print(("ok   " if ok else "FAIL ") + name + (f"  [{detail}]" if not ok and detail != "" else ""))
    if not ok:
        fails.append(name)


def fake_session():
    from aiogram.client.session.base import BaseSession
    from aiogram.types import Chat, Document, Message, PhotoSize, Video

    class S(BaseSession):
        def __init__(self):
            super().__init__()
            self.calls = []

        async def close(self):
            pass

        async def stream_content(self, *a, **k):
            yield b""

        async def make_request(self, bot, method, timeout=None):
            self.calls.append(method)
            name = type(method).__name__
            extra = {}
            if name == "SendPhoto":
                extra["photo"] = [PhotoSize(file_id="PHOTO-ID", file_unique_id="p", width=10, height=10)]
            if name == "SendVideo":
                extra["video"] = Video(file_id="VIDEO-ID", file_unique_id="v", width=10, height=10, duration=3)
            if name == "SendDocument":
                extra["document"] = Document(file_id="DOC-ID", file_unique_id="d")
            return Message(message_id=len(self.calls), date=datetime.now(timezone.utc),
                           chat=Chat(id=getattr(method, "chat_id", 1) or 1, type="private"), text="x", **extra)

    return S()


def png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (91, 242, 166)).save(buf, "PNG")
    return buf.getvalue()


def labels(markup) -> list[str]:
    return [b.text for row in (markup.inline_keyboard if markup else []) for b in row]


async def main() -> int:
    from aiogram import Bot

    from bot.keyboards.entry_kb import onboarding_kb
    from webapp import panel

    kb = onboarding_kb("uz")
    # 1. text only, with the buttons
    s = fake_session()
    bot = Bot("8888548766:TEST", session=s)
    await panel._deliver(bot, 1, "Salom", None, kb, {})
    check("text only: one message, the Writing / Speaking buttons under it",
          [type(c).__name__ for c in s.calls] == ["SendMessage"] and any("Writing" in x for x in labels(s.calls[0].reply_markup))
          and any("Speaking" in x for x in labels(s.calls[0].reply_markup)), labels(s.calls[0].reply_markup))

    # 2. a picture with a short text: one post; the second student gets Telegram's file id, not a new upload
    s, cache = fake_session(), {}
    bot = Bot("8888548766:TEST", session=s)
    media = ("photo", png(), "post.png")
    await panel._deliver(bot, 1, "🎁 8 coin", media, kb, cache)
    await panel._deliver(bot, 2, "🎁 8 coin", media, kb, cache)
    a, b = s.calls
    check("picture + text: a photo post with the text as its caption and the buttons",
          type(a).__name__ == "SendPhoto" and a.caption == "🎁 8 coin" and labels(a.reply_markup) == labels(kb))
    check("...uploaded once: the next student gets the file id", b.photo == "PHOTO-ID" and cache.get("id") == "PHOTO-ID",
          (b.photo, cache))

    # 3. a video with a text over the caption limit: the video, then the text with the buttons
    s, cache = fake_session(), {}
    bot = Bot("8888548766:TEST", session=s)
    long = "a" * (panel.BROADCAST_CAPTION + 1)
    await panel._deliver(bot, 1, long, ("video", b"\x00\x00\x00\x18ftypmp42", "v.mp4"), kb, cache)
    names = [type(c).__name__ for c in s.calls]
    check("video + a long text: the video alone, then the text with the buttons",
          names == ["SendVideo", "SendMessage"] and s.calls[0].caption is None and s.calls[1].text == long
          and labels(s.calls[1].reply_markup) == labels(kb) and cache.get("id") == "VIDEO-ID", names)

    # 4. the page: the form sends files, the picture is checked, a wrong type or a big file is refused
    from webapp.main import app

    sent = []

    async def fake_job(bid, targets, texts, media=None, buttons=False):
        sent.append((targets, texts, media, buttons))

    panel._broadcast_job = fake_job
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://testserver") as owner:
        await owner.post("/login", data={"username": settings.web_admin_username,
                                         "password": settings.web_admin_password})
        page = (await owner.get("/broadcast")).text
        check("the page: the form takes a file, a picture/video field and the buttons tick",
              'enctype="multipart/form-data"' in page and 'name="media"' in page and 'name="buttons"' in page)
        form = {"audience": "admins", "lang": "any", "text_uz": "Rasmli post", "buttons": "1"}
        r = await owner.post("/broadcast/send", data=form, files={"media": ("post.png", png(), "image/png")},
                             follow_redirects=False)
        await asyncio.sleep(0.05)
        check("a picture post with the buttons: started, the job gets the photo and the buttons",
              r.status_code == 303 and "started=" in r.headers["location"] and sent
              and sent[-1][2][0] == "photo" and sent[-1][2][1][:8] == b"\x89PNG\r\n\x1a\n" and sent[-1][3] is True,
              (r.status_code, r.headers.get("location")))
        n = len(sent)
        r = await owner.post("/broadcast/send", data={**form, "text_uz": "Boshqa"},
                             files={"media": ("a.gif", b"GIF89a", "image/gif")}, follow_redirects=False)
        check("a GIF: refused with the reason, nothing sent", "started=mediatype" in r.headers["location"] and len(sent) == n)
        big = b"\xff\xd8\xff" + b"0" * (panel.MEDIA_LIMITS["photo"] + 1)
        r = await owner.post("/broadcast/send", data={**form, "text_uz": "Katta"},
                             files={"media": ("big.jpg", big, "image/jpeg")}, follow_redirects=False)
        check("a picture over 10 MB: refused with the reason", "started=mediabig" in r.headers["location"] and len(sent) == n)
        r = await owner.post("/broadcast/send", data={"audience": "admins", "lang": "any", "text_uz": "Faqat matn"},
                             follow_redirects=False)
        await asyncio.sleep(0.05)
        check("no file, no tick: a plain text as before", "started=" in r.headers["location"] and len(sent) == n + 1
              and sent[-1][2] is None and sent[-1][3] is False)
        hist = (await owner.get("/broadcast")).text
        check("the history marks the picture post and its buttons", "🖼 Rasm" in hist and "🔘 Tugmalar" in hist)

    print(f"\n{len(fails)} failed" if fails else "\nall passed")
    from bot.utils.crm import redis

    await redis().aclose()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
