# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Prevent Python from buffering stdout/stderr and writing .pyc files
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install system dependencies for C extensions (e.g. psycopg2) and networking
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for optimal Docker layer caching
COPY module_1/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application assets and dashboard code
COPY module_1/www/ /app/www/
COPY module_1/app.py /app/app.py

# Create unprivileged application user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose default port (overridden dynamically by Render via $PORT)
EXPOSE 8000

# Start Shiny application binding to 0.0.0.0 and dynamically expanding $PORT
CMD ["sh", "-c", "shiny run --host 0.0.0.0 --port ${PORT:-8000} app.py"]
