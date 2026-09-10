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
import os
import re
import socket
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, quote

import requests

from . import config

log = logging.getLogger(__name__)

# Timeout for proxy validation checks
_VALIDATE_TIMEOUT = 5  # seconds
_VALIDATE_URL = "https://api.ipify.org"
_CACHE_TTL = 600  # seconds to cache successful validation


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
    """
    Thread-safe round-robin proxy manager.
    Supports:
      - Loading from config file AND environment variables (PROXIES / PROXY_URL)
      - Hot-reload without restarting (monitors file mtime automatically)
      - Pre-use validation before returning a proxy
      - Automatic eviction of failed proxies
    """

    def __init__(self, proxy_file: Path, default_scheme: str = "http") -> None:
        self._file = proxy_file
        self._scheme = default_scheme
        self._lock = threading.Lock()
        self._proxies: List[str] = []
        self._index: int = 0
        self._last_mtime: float = 0.0
        self._validated_cache: Dict[str, float] = {}
        self._load()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_next(self, validate: bool = True) -> Optional[str]:
        """
        Return the next proxy URL (round-robin).
        If validate=True, checks connectivity before returning it.
        """
        self._check_file_update()

        with self._lock:
            if not self._proxies:
                return None

            attempts = len(self._proxies)
            for _ in range(attempts):
                proxy = self._proxies[self._index % len(self._proxies)]
                self._index += 1

                if not validate:
                    return proxy

                # Check if validated recently
                now = time.monotonic()
                if proxy in self._validated_cache and (now - self._validated_cache[proxy]) < _CACHE_TTL:
                    return proxy

                # Validate connectivity before use
                log.info("Validating proxy before use: %s", proxy)
                if validate_proxy(proxy):
                    self._validated_cache[proxy] = now
                    log.info("Proxy validated successfully ✓")
                    return proxy
                else:
                    log.warning("Proxy failed validation check before use, evicting: %s", proxy)
                    try:
                        self._proxies.remove(proxy)
                    except ValueError:
                        pass

            return None

    def mark_failed(self, proxy_url: str) -> None:
        """Remove a proxy that has been confirmed broken."""
        with self._lock:
            try:
                self._proxies.remove(proxy_url)
                self._validated_cache.pop(proxy_url, None)
                log.warning("Removed failed proxy: %s  (%d remaining)", proxy_url, len(self._proxies))
            except ValueError:
                pass  # Already removed

    def reload(self) -> None:
        """Hot-reload the proxy source without restarting the service."""
        with self._lock:
            self._load()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._proxies)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _check_file_update(self) -> None:
        """Automatically hot-reload if the proxy file was modified on disk."""
        try:
            if self._file.exists():
                mtime = self._file.stat().st_mtime
                if mtime > self._last_mtime:
                    log.info("Proxy file modified on disk — hot-reloading proxies without restart")
                    with self._lock:
                        self._load()
        except Exception as exc:
            log.debug("Error checking proxy file update: %s", exc)

    def _load(self) -> None:
        """Load proxies from file and environment variables."""
        candidates: List[str] = []

        # 1. Load from environment variables (PROXIES or PROXY_URL)
        env_proxies = os.getenv("PROXIES") or os.getenv("PROXY_URL", "")
        if env_proxies:
            for item in re.split(r"[\n,;]+", env_proxies):
                url = _parse_line(item, self._scheme)
                if url and url not in candidates:
                    candidates.append(url)

        # 2. Load from proxy file
        if self._file.exists():
            try:
                self._last_mtime = self._file.stat().st_mtime
                raw_lines = self._file.read_text().splitlines()
                for line in raw_lines:
                    url = _parse_line(line, self._scheme)
                    if url and url not in candidates:
                        candidates.append(url)
            except Exception as exc:
                log.warning("Failed to read proxy file %s: %s", self._file, exc)
        else:
            if not candidates:
                log.warning("Proxy file not found (%s) and no PROXIES env var set", self._file)

        log.info("Loaded %d proxies (file=%s, env_vars=%s)", len(candidates), self._file, bool(env_proxies))
        self._proxies = candidates
        self._index = 0

    def validate_all(self) -> None:
        """Validate every proxy and remove unreachable ones."""
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
