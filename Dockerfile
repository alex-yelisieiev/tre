# ── Stage 1: builder ──────────────────────────────────────────────────────────
# Install Python deps normally (no --prefix) so cloakbrowser is importable,
# then pre-download the stealth Chromium binary to a fixed path.
FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --upgrade pip wheel

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the CloakBrowser binary to a fixed, known location.
# CLOAKBROWSER_CACHE_DIR tells the library where to put the binary.
# The `|| true` lets the build succeed even if the download fails
# (e.g. network timeout); it will be fetched on first container run instead.
# `mkdir -p` ensures the path exists so the later COPY always succeeds.
ENV CLOAKBROWSER_CACHE_DIR=/cb-cache
RUN python -c "from cloakbrowser._install import ensure_binary; ensure_binary()" || true \
 && mkdir -p /cb-cache


# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# System libraries required by Chromium + Xvfb
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core Chromium deps
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2t64 \
    libatspi2.0-0 \
    # Virtual display (headed mode inside Docker for maximum CF bypass)
    xvfb \
    x11-utils \
    # Fonts — both Linux + Windows sets for realistic fingerprint spoofing
    fonts-liberation \
    fonts-noto \
    fonts-noto-color-emoji \
    # Windows Core Fonts (Arial, Times New Roman, etc.) — strongly advised by CloakBrowser
    cabextract \
    wget \
    # TLS
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Windows Core Fonts (Arial, Verdana, Times New Roman, Courier, etc.)
# CloakBrowser warns that missing Windows fonts hurt detection scores on Linux.
RUN mkdir -p /usr/share/fonts/truetype/msttcorefonts \
 && for font in \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial.ttf \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial_Bold.ttf \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial_Bold_Italic.ttf \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Arial_Italic.ttf \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Times_New_Roman.ttf \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Times_New_Roman_Bold.ttf \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Times_New_Roman_Bold_Italic.ttf \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Times_New_Roman_Italic.ttf \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Verdana.ttf \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Verdana_Bold.ttf \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Verdana_Bold_Italic.ttf \
      https://github.com/matomo-org/travis-scripts/raw/master/fonts/Verdana_Italic.ttf \
    ; do \
      wget -q -P /usr/share/fonts/truetype/msttcorefonts "$font" || true ; \
    done \
 && fc-cache -fv \
 && apt-get purge -y --auto-remove wget cabextract \
 && rm -rf /var/lib/apt/lists/*

# Non-root user (created before COPY so we can chown)
RUN useradd -m -u 1000 scraper && mkdir -p /app/output

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy pre-downloaded CloakBrowser binary into the scraper user's home cache
# so the non-root process has read/write access without sudo.
COPY --from=builder /cb-cache /home/scraper/.cache/cloakbrowser
RUN chown -R scraper:scraper /home/scraper/.cache /app

WORKDIR /app
COPY src/ ./src/
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chown -R scraper:scraper /app

USER scraper

# Xvfb display number used by entrypoint
ENV DISPLAY=:99
# Disable auto-update so the baked binary is always used
ENV CLOAKBROWSER_SKIP_UPDATE=1
# Point the library at the scraper user's writable cache
ENV CLOAKBROWSER_CACHE_DIR=/home/scraper/.cache/cloakbrowser

ENTRYPOINT ["/app/docker-entrypoint.sh"]
