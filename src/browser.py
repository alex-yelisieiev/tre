import asyncio
from typing import Optional, Tuple
from cloakbrowser import launch_async
from playwright.async_api import Browser, BrowserContext, Page

from src.proxy_manager import ProxyInfo


class CloakEngine:
    def __init__(self, browser: Browser, context: BrowserContext, page: Page):
        self.browser = browser
        self.context = context
        self.page = page

    @classmethod
    async def launch(
        cls,
        proxy: Optional[ProxyInfo] = None,
        headless: bool = True,
        timezone: str = "America/New_York",
        locale: str = "en-US",
    ) -> "CloakEngine":
        proxy_url = proxy.http_url if proxy else None

        browser = await launch_async(
            headless=headless,
            proxy=proxy_url,
            humanize=True,
            timezone=timezone,
            locale=locale,
            args=[
                "--window-size=1920,1080",
                "--disable-dev-shm-usage",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale=locale,
            timezone_id=timezone,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.axs.com/",
            },
        )
        page = await context.new_page()
        return cls(browser=browser, context=context, page=page)

    async def close(self) -> None:
        try:
            await self.context.close()
            await self.browser.close()
        except Exception:
            pass
