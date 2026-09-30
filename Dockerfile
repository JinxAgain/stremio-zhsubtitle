FROM python:3.11-slim

# Install system utilities (curl, unar, and official 7-Zip 7zz with native RAR5 support)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates xz-utils unar && \
    curl -fsSL https://www.7-zip.org/a/7z2301-linux-x64.tar.xz | tar -xJf - -C /usr/local/bin 7zz 7zzs && \
    chmod +x /usr/local/bin/7zz /usr/local/bin/7zzs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY app ./app
COPY static ./static

# Default environment configuration (7860 default for Hugging Face Spaces, can be overridden)
ENV HOST=0.0.0.0
ENV PORT=7860
ENV CACHE_DIR=/app/data/cache
ENV PYTHONUNBUFFERED=1

EXPOSE 7860

# Launch uvicorn dynamically bound to $PORT
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
