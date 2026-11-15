"""PDF / DOCX -> plain text extraction.

PDF pages with little or no embedded text (i.e. scanned images / photographed
pages) are rasterized and routed through the same GPT-4o vision OCR used for
plain photo uploads, so handwritten or scanned submissions are handled
transparently either way.

Since the 2026-11-15 audit: a PDF over MAX_PDF_PAGES is turned down before
anything is read (a whole book sent by mistake rendered every page at 200 dpi
and OCR'd each scanned one - minutes of a frozen bot and a model call per
page); the parsing and the rendering run off the event loop; only the scanned
pages are rendered.
"""
from __future__ import annotations

import asyncio
import io

from docx import Document
from pdf2image import convert_from_bytes
from pypdf import PdfReader

from services.ai.ocr import extract_text_from_image

MIN_CHARS_PER_PAGE_BEFORE_OCR_FALLBACK = 20
MAX_PDF_PAGES = 6  # an IELTS question or answer is 1-3 pages


class TooManyPages(Exception):
    def __init__(self, pages: int):
        super().__init__(f"{pages} pages")
        self.pages = pages


def _read_pdf(pdf_bytes: bytes) -> tuple[list[str], list[int]]:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if len(reader.pages) > MAX_PDF_PAGES:
        raise TooManyPages(len(reader.pages))
    pages_text: list[str] = []
    scanned: list[int] = []
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if len(text) < MIN_CHARS_PER_PAGE_BEFORE_OCR_FALLBACK:
            pages_text.append("")  # filled in below if this turns out to be a scanned page
            scanned.append(i)
        else:
            pages_text.append(text)
    return pages_text, scanned


def _render_page(pdf_bytes: bytes, index: int) -> bytes:
    image = convert_from_bytes(pdf_bytes, dpi=200, first_page=index + 1, last_page=index + 1)[0]
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


async def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    pages_text, scanned = await asyncio.to_thread(_read_pdf, pdf_bytes)
    for i in scanned:
        png = await asyncio.to_thread(_render_page, pdf_bytes, i)
        pages_text[i] = await extract_text_from_image(png, mime_type="image/png")
    return "\n\n".join(p for p in pages_text if p).strip()


def extract_text_from_docx(docx_bytes: bytes) -> str:
    document = Document(io.BytesIO(docx_bytes))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    return "\n".join(paragraphs).strip()
