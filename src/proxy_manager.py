from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Protocol
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

VALIDATE_TIMEOUT = 5
VALIDATE_URL = "https://api.ipify.org"
CACHE_TTL = 600


class ProxyProvider(Protocol):
    def get_next(self, validate: bool = True) -> Optional[str]:
        ...

    def mark_failed(self, proxy_url: str) -> None:
        ...

    @property
    def count(self) -> int:
        ...


def parse_proxy_line(line: str, default_scheme: str = "http") -> Optional[str]:
    cleaned = line.strip()
    if not cleaned or cleaned.startswith("#"):
        return None

    if re.match(r"^(http|https|socks5)://", cleaned):
        return cleaned

    return f"{default_scheme}://{cleaned}"


def build_requests_proxies(proxy_url: str) -> dict[str, str]:
    parsed = urlparse(proxy_url)
    scheme = parsed.scheme

    if scheme in {"http", "https"}:
        return {"http": proxy_url, "https": proxy_url}

    if scheme == "socks5":
        socks_url = proxy_url.replace("socks5://", "socks5h://", 1)
        return {"http": socks_url, "https": socks_url}

    return {}


def validate_proxy(proxy_url: str, timeout: int = VALIDATE_TIMEOUT) -> bool:
    proxies = build_requests_proxies(proxy_url)
    try:
        response = requests.get(VALIDATE_URL, proxies=proxies, timeout=timeout)
        return response.status_code == 200
    except Exception as exc:
        log.debug("Validation failed for %s: %s", proxy_url, exc)
        return False


class ProxyManager:
    def __init__(self, proxy_file: Path, default_scheme: str = "http") -> None:
        self._file = proxy_file
        self._scheme = default_scheme
        self._lock = threading.Lock()
        self._proxies: List[str] = []
        self._index: int = 0
        self._last_mtime: float = 0.0
        self._validated_cache: Dict[str, float] = {}
        self._load()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._proxies)

    def get_next(self, validate: bool = True) -> Optional[str]:
        self._reload_if_modified()

        with self._lock:
            if not self._proxies:
                return None

            candidates_count = len(self._proxies)
            for _ in range(candidates_count):
                proxy = self._proxies[self._index % len(self._proxies)]
                self._index += 1

                if not validate or self._is_cache_valid(proxy):
                    return proxy

                log.info("Validating proxy before use: %s", proxy)
                if validate_proxy(proxy):
                    self._validated_cache[proxy] = time.monotonic()
                    log.info("Proxy validated successfully")
                    return proxy

                log.warning("Proxy validation failed, evicting: %s", proxy)
                try:
                    self._proxies.remove(proxy)
                except ValueError:
                    pass

            return None

    def mark_failed(self, proxy_url: str) -> None:
        with self._lock:
            try:
                self._proxies.remove(proxy_url)
                self._validated_cache.pop(proxy_url, None)
                log.warning("Removed failed proxy: %s (%d remaining)", proxy_url, len(self._proxies))
            except ValueError:
                pass

    def reload(self) -> None:
        with self._lock:
            self._load()

    def _is_cache_valid(self, proxy: str) -> bool:
        last_validated = self._validated_cache.get(proxy)
        if last_validated is None:
            return False
        return (time.monotonic() - last_validated) < CACHE_TTL

    def _reload_if_modified(self) -> None:
        try:
            if self._file.exists():
                mtime = self._file.stat().st_mtime
                if mtime > self._last_mtime:
                    log.info("Proxy file updated on disk, reloading")
                    with self._lock:
                        self._load()
        except Exception as exc:
            log.debug("Error checking proxy file update: %s", exc)

    def _load(self) -> None:
        candidates: List[str] = []

        env_proxies = os.getenv("PROXIES") or os.getenv("PROXY_URL", "")
        if env_proxies:
            for entry in re.split(r"[\n,;]+", env_proxies):
                parsed = parse_proxy_line(entry, self._scheme)
                if parsed and parsed not in candidates:
                    candidates.append(parsed)

        if self._file.exists():
            try:
                self._last_mtime = self._file.stat().st_mtime
                for line in self._file.read_text(encoding="utf-8").splitlines():
                    parsed = parse_proxy_line(line, self._scheme)
                    if parsed and parsed not in candidates:
                        candidates.append(parsed)
            except Exception as exc:
                log.warning("Failed to read proxy file %s: %s", self._file, exc)
        elif not candidates:
            log.warning("Proxy file not found (%s) and no PROXIES env var provided", self._file)

        log.info("Loaded %d proxies (file=%s)", len(candidates), self._file)
        self._proxies = candidates
        self._index = 0
