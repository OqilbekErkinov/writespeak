"""Reads public Telegram channels through their web preview (t.me/s/<channel>),
so the importer needs no Telegram account or API keys.

A preview page lists up to ~20 posts, newest last; `?before=<id>` pages
backwards. Each post gives its text (as HTML and as plain text), its date,
whether it was edited, and its photos (CDN links, about 800 px wide - enough
for a Task 1 chart).
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

import httpx  # installed with openai

logger = logging.getLogger(__name__)

BASE = "https://t.me/s/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BandzillaBot/1.0; +https://bandzilla.uz)"}
PAGE_DELAY = 0.7  # seconds between page requests - be gentle with t.me
RETRIES = 3

_POST_RX = re.compile(r'<div class="tgme_widget_message [^"]*" data-post="([^"/]+)/(\d+)"')
_TEXT_RX = re.compile(r'<div class="tgme_widget_message_text js-message_text"[^>]*>(.*?)</div>', re.S)
_PHOTO_RX = re.compile(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)")
_DATE_RX = re.compile(r'<time datetime="([^"]+)"')
_LINK_RX = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)


@dataclass
class ChannelPost:
    channel: str
    id: int
    date: datetime | None
    text: str
    html: str
    image_urls: list[str] = field(default_factory=list)
    edited: bool = False

    def links(self) -> list[tuple[str, str]]:
        """(url, plain link text) of each link in the text, in order."""
        return [(html.unescape(url), html_to_text(label)) for url, label in _LINK_RX.findall(self.html)]


def html_to_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).replace("\xa0", " ").strip()


def parse_page(page: str) -> list[ChannelPost]:
    starts = list(_POST_RX.finditer(page))
    posts = []
    for i, m in enumerate(starts):
        body = page[m.end() : starts[i + 1].start() if i + 1 < len(starts) else len(page)]
        text_match = _TEXT_RX.search(body)
        inner = text_match.group(1) if text_match else ""
        date_match = _DATE_RX.search(body)
        posts.append(
            ChannelPost(
                channel=m.group(1),
                id=int(m.group(2)),
                date=datetime.fromisoformat(date_match.group(1)) if date_match else None,
                text=html_to_text(inner),
                html=inner,
                image_urls=_PHOTO_RX.findall(body),
                edited='tgme_widget_message_meta">edited' in body,
            )
        )
    return posts


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    for attempt in range(1, RETRIES + 1):
        try:
            response = await client.get(url)
            if response.status_code == 200:
                return response
            if response.status_code not in (429, 500, 502, 503, 504):
                response.raise_for_status()
            logger.warning("%s -> HTTP %s (attempt %s)", url, response.status_code, attempt)
        except httpx.TransportError as e:
            logger.warning("%s -> %s (attempt %s)", url, e, attempt)
        await asyncio.sleep(3 * attempt)
    raise RuntimeError(f"could not fetch {url}")


def new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True)


async def fetch_page(client: httpx.AsyncClient, channel: str, before: int | None = None) -> list[ChannelPost]:
    url = BASE + channel + (f"?before={before}" if before else "")
    posts = parse_page((await _get(client, url)).text)
    await asyncio.sleep(PAGE_DELAY)
    return posts


async def fetch_since(
    client: httpx.AsyncClient, channel: str, *, min_id: int = 0, since: datetime | None = None, max_pages: int = 400
) -> list[ChannelPost]:
    """Posts newer than `min_id` (and not older than `since`), oldest first."""
    found: dict[int, ChannelPost] = {}
    before = None
    for _ in range(max_pages):
        page = await fetch_page(client, channel, before)
        if not page:
            break
        for post in page:
            found[post.id] = post
        oldest = min(p.id for p in page)
        if oldest <= min_id + 1 or oldest <= 1:
            break
        if since is not None and all(p.date is not None and p.date < since for p in page):
            break
        before = oldest
    posts = [p for p in found.values() if p.id > min_id and (since is None or p.date is None or p.date >= since)]
    return sorted(posts, key=lambda p: p.id)


async def fetch_ids(client: httpx.AsyncClient, channel: str, ids: set[int]) -> dict[int, ChannelPost]:
    """The given posts (those that still exist), paging back from the newest."""
    found: dict[int, ChannelPost] = {}
    missing = set(ids)
    before = max(ids) + 1 if ids else None
    while missing and before:
        page = await fetch_page(client, channel, before)
        if not page:
            break
        for post in page:
            if post.id in missing:
                found[post.id] = post
        oldest = min(p.id for p in page)
        missing = {i for i in missing if i < oldest}
        before = oldest if oldest > 1 else None
    return found


async def download(client: httpx.AsyncClient, url: str) -> bytes:
    return (await _get(client, url)).content
