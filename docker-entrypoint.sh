#!/usr/bin/env bash
# docker-entrypoint.sh — start Xvfb then run the scraper

set -euo pipefail

# Start a virtual framebuffer so CloakBrowser can run in "headed" mode
# (headless=False) inside Docker, which maximises Cloudflare bypass success.
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
export DISPLAY=:99

# Give Xvfb a moment to initialise
sleep 1

cleanup() {
    kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT

exec python -m src.scraper "$@"
