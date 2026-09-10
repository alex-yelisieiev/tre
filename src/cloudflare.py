import asyncio
import time
from typing import Optional
import nodriver as uc

from src.browser import StealthBrowser


class CloudflareSolver:
    def __init__(self, browser: uc.Browser, timeout: int = 35):
        self.browser = browser
        self.timeout = timeout

    async def is_challenge_present(self, tab: uc.Tab) -> bool:
        try:
            res = await tab.evaluate("""(() => {
                const title = document.title ? document.title.toLowerCase() : '';
                const bodyText = document.body ? document.body.innerText.toLowerCase() : '';
                const hasIframe = !!document.querySelector("iframe[src*='challenges.cloudflare.com']");
                const hasStage = !!document.querySelector("#challenge-stage, #cf-stage, .ctp-checkbox-label");
                
                const isChallenge = (
                    title.includes('just a moment') ||
                    title.includes('attention required') ||
                    title.includes('security check') ||
                    bodyText.includes('checking your browser') ||
                    bodyText.includes('verify you are human') ||
                    hasIframe ||
                    hasStage
                );
                return isChallenge;
            })()""")
            return bool(res)
        except Exception:
            return False

    async def get_turnstile_box(self, tab: uc.Tab) -> Optional[dict]:
        try:
            box = await tab.evaluate("""(() => {
                // Check iframes for Cloudflare Turnstile
                const iframes = Array.from(document.querySelectorAll("iframe"));
                for (const f of iframes) {
                    if (f.src && f.src.includes("challenges.cloudflare.com")) {
                        const rect = f.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            return {
                                found: true,
                                x: rect.x + 30,
                                y: rect.y + rect.height / 2
                            };
                        }
                    }
                }

                // Check challenge stage container
                const stage = document.querySelector("#challenge-stage") || document.querySelector(".ctp-checkbox-label");
                if (stage) {
                    const rect = stage.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                        return {
                            found: true,
                            x: rect.x + 25,
                            y: rect.y + rect.height / 2
                        };
                    }
                }
                return { found: false };
            })()""")
            if box and box.get("found"):
                return box
        except Exception:
            pass
        return None

    async def solve(self, tab: uc.Tab) -> bool:
        start_time = time.time()

        while time.time() - start_time < self.timeout:
            challenge_active = await self.is_challenge_present(tab)
            if not challenge_active:
                return True

            box = await self.get_turnstile_box(tab)
            if box:
                # Add human hesitation
                await StealthBrowser.human_delay(0.6, 1.2)
                # Dispatch human mouse trajectory and click
                await StealthBrowser.human_move_and_click(
                    tab=tab,
                    target_x=float(box["x"]),
                    target_y=float(box["y"]),
                )
                await asyncio.sleep(2.0)
            else:
                # Non-interactive challenge: wait for proof-of-work resolution
                await asyncio.sleep(1.0)

        # Final check
        return not (await self.is_challenge_present(tab))
