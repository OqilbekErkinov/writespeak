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
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

CMD ["python", "-m", "bot.main"]
