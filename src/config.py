from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _load_dotenv_if_present(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#") or "=" not in trimmed:
            continue
        key, _, val = trimmed.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        os.environ.setdefault(key, val)


def _parse_bool(value: Optional[str], default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _parse_int(value: Optional[str], default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    target_urls: tuple[str, ...]
    proxy_file: Path
    proxy_type: str
    action: str
    output_dir: Path
    headless: bool
    humanize: bool
    geoip: bool
    page_timeout_ms: int
    cf_wait_timeout_ms: int
    max_retries: int
    license_key: Optional[str]

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None) -> Config:
        target_env = env_file or Path(".env")
        _load_dotenv_if_present(target_env)

        default_urls = (
            "https://shop.axs.com/?c=axs&e=6414022407626854",
            "https://shop.axs.com/?c=axs&e=4436620017755968",
        )
        env_urls = os.getenv("TARGET_URLS")
        if env_urls:
            urls = tuple(u.strip() for u in env_urls.split(",") if u.strip())
        else:
            urls = default_urls

        output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
        output_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            target_urls=urls,
            proxy_file=Path(os.getenv("PROXY_FILE", "proxies.txt")),
            proxy_type=os.getenv("PROXY_TYPE", "http").lower(),
            action=os.getenv("ACTION", "both").lower(),
            output_dir=output_dir,
            headless=_parse_bool(os.getenv("HEADLESS"), default=False),
            humanize=_parse_bool(os.getenv("HUMANIZE"), default=True),
            geoip=_parse_bool(os.getenv("GEOIP"), default=True),
            page_timeout_ms=_parse_int(os.getenv("PAGE_TIMEOUT"), 60_000),
            cf_wait_timeout_ms=_parse_int(os.getenv("CF_WAIT_TIMEOUT"), 90) * 1000,
            max_retries=_parse_int(os.getenv("MAX_RETRIES"), 3),
            license_key=os.getenv("CLOAKBROWSER_LICENSE_KEY") or None,
        )
