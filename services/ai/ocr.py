"""Image / handwriting -> text, via GPT-4o vision.

Used directly for photo uploads, and as a fallback by document_parser.py for
scanned (image-only) PDF pages.
"""
from __future__ import annotations

import base64

from bot.config import settings
from services.ai import usage
from services.ai.openai_client import client

OCR_SYSTEM_PROMPT = (
    "You are a precise OCR engine specialized in reading both handwritten and typed English "
    "IELTS answers. Transcribe the text in the image EXACTLY as written, including the "
    "student's own spelling, grammar, and punctuation mistakes — do NOT correct, paraphrase, "
    "summarize, or omit anything, since this text will be graded as-is. If a word is truly "
    "illegible, write [illegible] in its place. Keep the writer's paragraphs: one blank line between "
    "paragraphs, and the lines of one paragraph joined with spaces (a line that only wraps is not a new "
    "paragraph) - the paragraphing is graded too. Output ONLY the transcribed text — no preamble, "
    "no commentary, no markdown formatting."
)

# A Task 1 question is often a photo of the whole task, chart included: only
# its wording is wanted - report #35's question box printed the chart's labels
# and figures read as text ("2008 21% 18% 15% ...") above the chart itself.
# The figures come from the chart (services/ai/visual_reading.py), never OCR.
TASK1_QUESTION_PROMPT = (
    "You read the question of an IELTS Academic Writing Task 1 from a photo or screenshot. Output ONLY the "
    "task's wording, word for word: the sentence(s) saying what the visual shows and the instructions printed "
    "with them (\"Summarise the information by selecting and reporting the main features...\", \"Write at least "
    "150 words.\").\n"
    "Leave out the visual itself and everything in it - its title, axis labels, legend, category names, years, "
    "numbers, percentages and data labels - and any app or page elements around it.\n"
    "If the image has no question wording at all (only the visual), write one plain sentence naming what it "
    "shows, e.g. \"The bar chart shows the average salaries of graduates in three universities.\"\n"
    "Output ONLY that text - no preamble, no commentary, no markdown."
)

# A Speaking question set is often a screenshot of an app or a channel post:
# only the examiner's questions are wanted, not the page around them (the
# user's report, 2026-10-18: "Answers here" buttons, a "2 questions" counter
# and a "Skills (Predicted & Reported: ...)" header were asked as questions).
SPEAKING_QUESTIONS_PROMPT = (
    "You read IELTS Speaking questions from a photo or screenshot (an app, a website, a book page, a "
    "Telegram post). Output ONLY the questions the examiner asks, word for word as printed, one "
    "question per line, in their order.\n"
    "Leave out everything else: app and website elements (buttons such as \"Answers here\" or \"Show "
    "answer\", badges such as \"NEW\" or \"Topic\", counters such as \"2 questions\", menus, likes, "
    "timestamps), topic, section and page headers (\"Skills (Predicted & Reported: September 2026 - "
    "April 2027)\", \"Part 1 - Hometown\"), dates, question numbers, sample answers, tips and "
    "watermarks.\n"
    "Keep an examiner's lead-in that belongs to the questions (\"Let's talk about your hometown.\") "
    "on its own line before them. A Part 2 cue card stays whole, line by line: the topic "
    "(\"Describe ...\"), \"You should say:\", each point, and the final \"and explain ...\" line.\n"
    "A question that runs over two printed lines is ONE question on one line; a short follow-up printed "
    "with it (\"Why?\", \"Why not?\") stays on its line.\n"
    "Change nothing inside a question: every word's spelling and every punctuation mark exactly as "
    "printed - British spellings stay British (\"travelling\", \"colour\", \"organise\"), nothing is "
    "corrected. If the image holds no IELTS question, write out its text as it is. Output ONLY that "
    "text - no preamble, no commentary, no markdown."
)

_MODES = {
    "verbatim": (OCR_SYSTEM_PROMPT, "Transcribe this image."),
    "speaking_questions": (SPEAKING_QUESTIONS_PROMPT, "Write out the IELTS Speaking question(s) in this image."),
    "writing_task1_question": (TASK1_QUESTION_PROMPT, "Write out the Task 1 question in this image."),
}


async def extract_text_from_image(image_bytes: bytes, mime_type: str = "image/jpeg", mode: str = "verbatim") -> str:
    """OCR a single image (photo of handwriting, or a rasterized PDF page).
    `mode`: "verbatim" (everything, as written - answers, Task 2 questions),
    "speaking_questions" (only the questions, no page around them) or
    "writing_task1_question" (only the task's wording, never the chart's own text)."""
    system, ask = _MODES[mode]
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    response = await client.chat.completions.create(
        model=settings.openai_model_vision,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": ask},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                ],
            },
        ],
        # No `temperature` override: gpt-5-family models only accept the
        # default value (1) and reject any other, per a live 400 test on
        # 2026-08-28 - see bot/config.py's model comment.
        # Sent as extra_body: openai 1.57 has no reasoning_effort argument yet.
        extra_body={"reasoning_effort": settings.openai_ocr_reasoning_effort},
    )
    usage.record("ocr", response)
    return (response.choices[0].message.content or "").strip()
