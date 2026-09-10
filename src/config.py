"""
Configuration loader.

All settings are read from environment variables (set in docker-compose.yml,
a .env file, or the shell). Defaults are chosen so the service works
out-of-the-box with the supplied IPRoyal proxies.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List


def _bool(val: str, default: bool = True) -> bool:
    return val.strip().lower() not in ("0", "false", "no", "off") if val else default


def _int(val: str, default: int) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ── Target URLs ──────────────────────────────────────────────────────────────
_DEFAULT_URLS = (
    "https://shop.axs.com/?c=axs&e=6414022407626854,"
    "https://shop.axs.com/?c=axs&e=4436620017755968"
)
TARGET_URLS: List[str] = [
    u.strip()
    for u in os.getenv("TARGET_URLS", _DEFAULT_URLS).split(",")
    if u.strip()
]

# ── Proxy ─────────────────────────────────────────────────────────────────────
PROXY_FILE: Path = Path(os.getenv("PROXY_FILE", "proxies.txt"))
PROXY_TYPE: str = os.getenv("PROXY_TYPE", "http").lower()  # http | https | socks5

# ── Action ────────────────────────────────────────────────────────────────────
# "screenshot"  – take a screenshot of the loaded page
# "add_to_cart" – attempt to add a ticket to the cart
# "both"        – do both (default)
ACTION: str = os.getenv("ACTION", "both").lower()

# ── Output ────────────────────────────────────────────────────────────────────
OUTPUT_DIR: Path = Path(os.getenv("OUTPUT_DIR", "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Browser ───────────────────────────────────────────────────────────────────
HEADLESS: bool = _bool(os.getenv("HEADLESS", "true"))
HUMANIZE: bool = _bool(os.getenv("HUMANIZE", "true"))
GEOIP: bool = _bool(os.getenv("GEOIP", "true"))
PAGE_TIMEOUT: int = _int(os.getenv("PAGE_TIMEOUT", "60000"), 60_000)   # ms

# ── Retry ─────────────────────────────────────────────────────────────────────
MAX_RETRIES: int = _int(os.getenv("MAX_RETRIES", "3"), 3)

# ── CloakBrowser license (Pro) ────────────────────────────────────────────────
# Set CLOAKBROWSER_LICENSE_KEY in the environment or .env file.
# If unset, the free build (Chromium 146 from GitHub Releases) is used.
LICENSE_KEY: str | None = os.getenv("CLOAKBROWSER_LICENSE_KEY") or None
