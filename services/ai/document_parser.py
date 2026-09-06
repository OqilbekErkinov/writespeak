"""PDF / DOCX -> plain text extraction.

PDF pages with little or no embedded text (i.e. scanned images / photographed
pages) are rasterized and routed through the same GPT-4o vision OCR used for
plain photo uploads, so handwritten or scanned submissions are handled
transparently either way.
"""
from __future__ import annotations

import io

from docx import Document
from pdf2image import convert_from_bytes
from pypdf import PdfReader

from services.ai.ocr import extract_text_from_image

MIN_CHARS_PER_PAGE_BEFORE_OCR_FALLBACK = 20


async def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages_text: list[str] = []
    scanned_page_indices: list[int] = []

    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if len(text) < MIN_CHARS_PER_PAGE_BEFORE_OCR_FALLBACK:
            pages_text.append("")  # filled in below if this turns out to be a scanned page
            scanned_page_indices.append(i)
        else:
            pages_text.append(text)

    if scanned_page_indices:
        images = convert_from_bytes(pdf_bytes, dpi=200)
        for i in scanned_page_indices:
            buf = io.BytesIO()
            images[i].save(buf, format="PNG")
            pages_text[i] = await extract_text_from_image(buf.getvalue(), mime_type="image/png")

    return "\n\n".join(p for p in pages_text if p).strip()


def extract_text_from_docx(docx_bytes: bytes) -> str:
    document = Document(io.BytesIO(docx_bytes))
    paragraphs = [p.text for p in document.paragraphs if p.text.strip()]
    return "\n".join(paragraphs).strip()
