from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Scraping Targets
    target_url: str = "https://shop.axs.com/?c=axs&e=6414022407626854"
    secondary_url: str = "https://shop.axs.com/?c=axs&e=4436620017755968"

    # Proxy Configuration
    proxy_file: Path = Path("test_data/iproyal-proxies (1).txt")
    single_proxy: Optional[str] = None
    proxy_timeout: float = 12.0
    proxy_validation_url: str = "https://api.ipify.org?format=json"

    # Browser & Anti-detect
    headless: bool = True
    browser_executable_path: Optional[str] = None
    navigation_timeout: int = 60
    challenge_timeout: int = 35
    max_retries: int = 3

    # Output Paths
    screenshot_dir: Path = Path("output/screenshots")
    log_dir: Path = Path("output/logs")

    def ensure_dirs(self) -> None:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
