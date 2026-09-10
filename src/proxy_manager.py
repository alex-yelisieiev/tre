"""
Proxy manager — load, validate, and rotate proxies.

Proxy file format (one per line):
    user:pass@host:port
    TEST_TASK_1:pass_country-us_session-X_lifetime-30m@geo.iproyal.com:12321
    http://user:pass@host:port
    socks5://user:pass@host:port

Lines starting with '#' and blank lines are ignored.
"""
from __future__ import annotations

import logging
import re
import socket
import threading
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse, quote

import requests

from . import config

log = logging.getLogger(__name__)

# Timeout for proxy validation checks
_VALIDATE_TIMEOUT = 10  # seconds
_VALIDATE_URL = "https://httpbin.org/ip"


def _parse_line(line: str, default_scheme: str) -> Optional[str]:
    """Return a fully-qualified proxy URL or None if the line is invalid."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    # Already has a scheme
    if re.match(r"^(http|https|socks5)://", line):
        return line

    # user:pass@host:port  (no scheme)
    return f"{default_scheme}://{line}"


def _build_requests_proxies(proxy_url: str) -> dict:
    """Build the dict expected by requests when using an authenticated proxy."""
    parsed = urlparse(proxy_url)
    scheme = parsed.scheme

    if scheme in ("http", "https"):
        return {"http": proxy_url, "https": proxy_url}

    if scheme == "socks5":
        # requests needs the socks5h scheme to resolve DNS through the proxy
        socks_url = proxy_url.replace("socks5://", "socks5h://", 1)
        return {"http": socks_url, "https": socks_url}

    return {}


def validate_proxy(proxy_url: str) -> bool:
    """Return True if the proxy can reach the internet."""
    proxies = _build_requests_proxies(proxy_url)
    try:
        resp = requests.get(
            _VALIDATE_URL,
            proxies=proxies,
            timeout=_VALIDATE_TIMEOUT,
        )
        return resp.status_code == 200
    except Exception as exc:
        log.debug("Proxy validation failed for %s: %s", proxy_url, exc)
        return False


class ProxyManager:
    """Thread-safe round-robin proxy manager with hot-reload support."""

    def __init__(self, proxy_file: Path, default_scheme: str = "http") -> None:
        self._file = proxy_file
        self._scheme = default_scheme
        self._lock = threading.Lock()
        self._proxies: List[str] = []
        self._index: int = 0
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_next(self) -> Optional[str]:
        """Return the next proxy URL (round-robin) or None if the list is empty."""
        with self._lock:
            if not self._proxies:
                return None
            proxy = self._proxies[self._index % len(self._proxies)]
            self._index += 1
            return proxy

    def mark_failed(self, proxy_url: str) -> None:
        """Remove a proxy that has been confirmed broken."""
        with self._lock:
            try:
                self._proxies.remove(proxy_url)
                log.warning("Removed failed proxy: %s  (%d remaining)", proxy_url, len(self._proxies))
            except ValueError:
                pass  # Already removed

    def reload(self) -> None:
        """Hot-reload the proxy file without restarting the service."""
        with self._lock:
            self._load(locked=False)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._proxies)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _load(self, locked: bool = True) -> None:
        """Parse the proxy file and (optionally) validate each entry."""
        if not self._file.exists():
            log.warning("Proxy file not found: %s", self._file)
            self._proxies = []
            return

        raw_lines = self._file.read_text().splitlines()
        candidates: List[str] = []

        for line in raw_lines:
            url = _parse_line(line, self._scheme)
            if url:
                candidates.append(url)

        log.info("Loaded %d proxies from %s", len(candidates), self._file)
        self._proxies = candidates
        self._index = 0

    def validate_all(self) -> None:
        """Validate every proxy and remove unreachable ones (can be slow)."""
        log.info("Validating %d proxies …", self.count)
        valid: List[str] = []
        for proxy in list(self._proxies):
            if validate_proxy(proxy):
                valid.append(proxy)
                log.info("  ✓  %s", proxy)
            else:
                log.warning("  ✗  %s  (removed)", proxy)
        with self._lock:
            self._proxies = valid
        log.info("Validation complete — %d valid proxies", len(valid))
