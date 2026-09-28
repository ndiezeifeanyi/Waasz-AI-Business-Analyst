FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Install system dependencies and build tools
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       tesseract-ocr \
       libpq5 \
       curl \
       build-essential \
       python3-dev \
       libpq-dev \
       libffi-dev \
       libssl-dev \
       pkg-config \
       libjpeg-dev \
       zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -v -r requirements.txt

# Copy application code
COPY . .

# Set ownership
RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# Run single consolidated FastAPI process with embedded background schedulers
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
