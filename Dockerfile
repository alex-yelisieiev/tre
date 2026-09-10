FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV CLOAKBROWSER_SUPPRESS_FONT_WARNING=1

WORKDIR /app

# Install base utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    ca-certificates \
    procps \
    fonts-liberation \
    fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright/Chromium system dependencies and pre-download CloakBrowser binary
RUN python -m playwright install-deps chromium \
    && python -c "import cloakbrowser; cloakbrowser.ensure_binary()" \
    && rm -rf /var/lib/apt/lists/*

# Copy application source
COPY src/ ./src/
COPY main.py .

# Create output directories
RUN mkdir -p /app/output/screenshots /app/output/logs

ENTRYPOINT ["python", "main.py"]
CMD ["--url", "https://shop.axs.com/?c=axs&e=6414022407626854"]
