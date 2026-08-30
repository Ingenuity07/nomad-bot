FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install postgres client build libraries and tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Copy and install python requirements
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright and install-deps chromium dynamically
RUN playwright install chromium
RUN playwright install-deps chromium

# Copy project files
COPY . /app/

# Ensure run script is executable
RUN chmod +x /app/scripts/run_worker.sh
