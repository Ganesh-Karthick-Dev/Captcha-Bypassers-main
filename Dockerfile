# Base image with Python 3.11 Debian Bookworm
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    BROWSER_HEADLESS=0 \
    PORT=5001

WORKDIR /app

# Install system dependencies:
# - xvfb & xauth: virtual display for headed browser operations
# - fonts: realistic font rendering for stealth browser fingerprints
# - Chromium runtime shared libraries required by CloakBrowser and Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    xauth \
    curl \
    wget \
    ca-certificates \
    procps \
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-freefont-ttf \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    libxshmfence1 \
    libglib2.0-0 \
    libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for build layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy all application source code
COPY . .

# Ensure entrypoint script has Unix line endings and execute permissions
RUN sed -i 's/\r$//' docker-entrypoint.sh && \
    chmod +x docker-entrypoint.sh

# Pre-fetch CloakBrowser Chromium binary during build (prevents runtime cold-start download)
RUN python -c "import sys; sys.path.insert(0, 'seo-rank-collector'); from cloakbrowser.download import ensure_binary; ensure_binary()" || true

# Expose microservice API port
EXPOSE 5001

# Run through Xvfb wrapper entrypoint
ENTRYPOINT ["/app/docker-entrypoint.sh"]
