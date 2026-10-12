FROM python:3.11-slim

# System dependencies for WeasyPrint (Pango/Cairo/GDK-Pixbuf) + fonts + Postgres client libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-noto \
    fonts-noto-color-emoji \
    fonts-inter \
    fontconfig \
    libpq-dev \
    gcc \
    poppler-utils \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# RapidOCR's models (~30 MB, services/practice_import/watermark.py) are fetched
# on first use; fetch them now so the image has them. Not fatal if the model
# host is unreachable - the importer then downloads them when it first runs.
RUN python -c "from rapidocr import RapidOCR; RapidOCR()" || echo "RapidOCR models will be downloaded on first use"

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

CMD ["python", "-m", "bot.main"]
