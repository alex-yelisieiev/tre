import asyncio
import json
import time
from datetime import datetime
from typing import Any, Dict, Optional

from src.browser import CloakEngine
from src.config import Settings
from src.proxy_manager import ProxyInfo, ProxyManager


class AXSScraper:
    def __init__(self, settings: Settings, proxy_manager: ProxyManager):
        self.settings = settings
        self.proxy_manager = proxy_manager
        self.settings.ensure_dirs()

    async def run(self, target_url: Optional[str] = None) -> Dict[str, Any]:
        url = target_url or self.settings.target_url
        last_error = None

        for attempt in range(1, self.settings.max_retries + 1):
            engine: Optional[CloakEngine] = None
            active_proxy: Optional[ProxyInfo] = None
            start_time = time.time()

            try:
                # 1. Acquire validated proxy
                active_proxy = await self.proxy_manager.get_working_proxy()

                # 2. Launch CloakBrowser
                engine = await CloakEngine.launch(
                    proxy=active_proxy,
                    headless=self.settings.headless,
                )
                page = engine.page

                # 3. Warm-up navigation to establish baseline session
                try:
                    await page.goto("https://www.axs.com/", wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(2.0)
                except Exception:
                    pass

                # 4. Navigate to target event page
                await page.goto(url, wait_until="domcontentloaded", timeout=self.settings.navigation_timeout * 1000)

                # 5. Wait for Cloudflare verification to resolve and redirect
                waited = 0
                while waited < self.settings.challenge_timeout:
                    title = await page.title()
                    lower_title = title.lower()
                    if "just a moment" not in lower_title and "verification in progress" not in lower_title:
                        break
                    # If interactive turnstile is displayed, attempt click
                    iframe = page.frame(url=lambda u: "challenges.cloudflare.com" in u)
                    if iframe:
                        cb = await iframe.query_selector("input[type='checkbox'], .ctp-checkbox-label")
                        if cb:
                            await cb.click(delay=150)
                    await asyncio.sleep(2.0)
                    waited += 2

                # Allow destination page to settle
                await asyncio.sleep(3.5)

                # 6. Capture screenshot
                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                screenshot_filename = f"axs_success_{timestamp}.png"
                screenshot_path = self.settings.screenshot_dir / screenshot_filename

                await page.screenshot(path=str(screenshot_path), full_page=False)

                page_title = await page.title()
                final_url = page.url
                elapsed = round(time.time() - start_time, 2)

                log_data = {
                    "status": "success",
                    "url": url,
                    "final_url": final_url,
                    "page_title": page_title,
                    "attempt": attempt,
                    "elapsed_seconds": elapsed,
                    "proxy": {
                        "host": active_proxy.host,
                        "port": active_proxy.port,
                        "session": active_proxy.session_id,
                    },
                    "screenshot": str(screenshot_path),
                    "timestamp": datetime.utcnow().isoformat(),
                }
                log_path = self.settings.log_dir / f"run_{timestamp}.json"
                log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")

                return log_data

            except Exception as e:
                last_error = e
                if active_proxy and active_proxy.session_id:
                    self.proxy_manager.renew_sticky_session(active_proxy)
                await asyncio.sleep(1.5)

            finally:
                if engine:
                    await engine.close()

        raise RuntimeError(f"All {self.settings.max_retries} attempts failed. Last error: {last_error}")
