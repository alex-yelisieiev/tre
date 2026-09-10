from __future__ import annotations

import json
import logging
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import Config
from .proxy_manager import ProxyManager, ProxyProvider

log = logging.getLogger("axs_scraper")


class TurnstileSolver:
    def is_present(self, page: Any) -> bool:
        try:
            cf_frame = page.frame_locator("iframe[title*='challenge' i]")
            cf_frame.locator("body").wait_for(timeout=500)
            return True
        except Exception:
            pass

        try:
            title = (page.title() or "").lower()
            return "just a moment" in title or "checking your browser" in title
        except Exception:
            return False

    def solve(self, page: Any, timeout_ms: int, humanize: bool = True) -> bool:
        deadline = time.monotonic() + (timeout_ms / 1000)
        elapsed = 0
        click_interval = 5
        next_click_at = 2

        while time.monotonic() < deadline:
            if not self.is_present(page):
                log.info("Cloudflare challenge cleared (after ~%ds)", elapsed)
                return True

            if elapsed % 10 == 0:
                log.info("Cloudflare challenge active (%ds elapsed)", elapsed)

            if elapsed >= next_click_at:
                if self._try_click(page, humanize):
                    log.info("Turnstile click sent at %ds", elapsed)
                    time.sleep(random.uniform(2.0, 4.0))
                    elapsed += 3
                    next_click_at = elapsed + click_interval
                    continue

            time.sleep(1.0)
            elapsed += 1

        log.warning("Cloudflare challenge did not clear within %ds", timeout_ms // 1000)
        return False

    def _try_click(self, page: Any, humanize: bool) -> bool:
        host_selectors = [
            "iframe[src*='challenges.cloudflare.com']",
            "iframe[title*='Widget containing a Cloudflare' i]",
            "iframe[title*='Widget' i]",
            "iframe[title*='challenge' i]",
        ]

        for selector in host_selectors:
            try:
                iframe = page.locator(selector).first
                iframe.wait_for(state="visible", timeout=4000)
                box = iframe.bounding_box()
                if box:
                    if humanize:
                        iframe.hover()
                        time.sleep(random.uniform(0.2, 0.5))
                    page.mouse.click(
                        box["x"] + min(24, box["width"] * 0.15),
                        box["y"] + (box["height"] / 2),
                    )
                    return True
            except Exception:
                continue

        for selector in host_selectors:
            try:
                cf_frame = page.frame_locator(selector)
                for inner in (
                    "[role='checkbox']",
                    ".ctp-checkbox-label",
                    ".cb-lb",
                    "input[type='checkbox']",
                    "label",
                    "body",
                ):
                    try:
                        loc = cf_frame.locator(inner).first
                        loc.wait_for(state="visible", timeout=1500)
                        if humanize:
                            loc.hover()
                            time.sleep(random.uniform(0.2, 0.4))
                        loc.click()
                        return True
                    except Exception:
                        continue
            except Exception:
                continue

        try:
            cf_frames = [
                frame for frame in page.frames
                if frame.url and "challenges.cloudflare.com" in frame.url
            ]
            if cf_frames:
                cf_frames[0].evaluate("""() => {
                    const selectors = [
                        'input[type="checkbox"]',
                        '[role="checkbox"]',
                        '.ctp-checkbox-label',
                        '.cb-lb',
                        '#checkbox',
                        'label'
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el) { el.click(); return sel; }
                    }
                    document.body.click();
                    return 'body';
                }""")
                return True
        except Exception:
            pass

        return False


class AXSScraper:
    def __init__(
        self,
        config: Config,
        proxy_provider: ProxyProvider,
        solver: Optional[TurnstileSolver] = None,
    ) -> None:
        self._config = config
        self._proxies = proxy_provider
        self._solver = solver or TurnstileSolver()

    def run(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for url in self._config.target_urls:
            outcome = self.scrape_url(url)
            results.append(outcome)
            status_text = "SUCCESS" if outcome["success"] else "FAILED"
            log.info("%s: %s", status_text, url)
        return results

    def scrape_url(self, url: str) -> Dict[str, Any]:
        from cloakbrowser import launch

        for attempt in range(1, self._config.max_retries + 1):
            proxy = self._proxies.get_next()
            log.info("Attempt %d/%d for %s (proxy=%s)", attempt, self._config.max_retries, url, proxy)

            browser = None
            try:
                launch_kwargs = self._build_launch_kwargs(proxy)
                browser = launch(**launch_kwargs)
                page = browser.new_page()
                page.set_default_timeout(self._config.page_timeout_ms)

                page.goto(url, wait_until="domcontentloaded", timeout=self._config.page_timeout_ms)
                time.sleep(random.uniform(1.5, 3.0))

                challenge_resolved = self._solver.solve(
                    page,
                    timeout_ms=self._config.cf_wait_timeout_ms,
                    humanize=self._config.humanize,
                )
                if not challenge_resolved:
                    self._take_screenshot(page, f"{url}_cf_blocked")
                    log.info("Cloudflare challenge unresolved, rotating proxy")
                    continue

                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass

                if self._is_access_restricted(page):
                    log.warning("AXS access restricted on proxy %s, rotating", proxy)
                    self._take_screenshot(page, f"{url}_restricted")
                    if proxy:
                        self._proxies.mark_failed(proxy)
                    continue

                self._human_scroll(page)
                time.sleep(random.uniform(0.5, 1.5))

                result: Dict[str, Any] = {
                    "url": url,
                    "proxy": proxy,
                    "attempt": attempt,
                    "success": True,
                    "screenshot": None,
                    "cart_added": False,
                    "error": None,
                }

                if self._config.action in {"screenshot", "both"}:
                    shot_path = self._take_screenshot(page, url)
                    result["screenshot"] = str(shot_path)

                if self._config.action in {"add_to_cart", "both"}:
                    result["cart_added"] = self._try_add_to_cart(page, url)
                    if self._config.action == "add_to_cart":
                        shot_path = self._take_screenshot(page, url)
                        result["screenshot"] = str(shot_path)

                return result

            except Exception as exc:
                err_message = str(exc)
                log.error("Attempt %d failed: %s", attempt, exc, exc_info=True)
                if proxy and self._is_proxy_connection_error(err_message):
                    self._proxies.mark_failed(proxy)

            finally:
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass

        return {
            "url": url,
            "proxy": None,
            "attempt": self._config.max_retries,
            "success": False,
            "screenshot": None,
            "cart_added": False,
            "error": f"All {self._config.max_retries} attempts failed",
        }

    def _build_launch_kwargs(self, proxy_url: Optional[str]) -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "headless": self._config.headless,
            "humanize": self._config.humanize,
            "human_preset": "careful",
        }
        if proxy_url:
            kwargs["proxy"] = proxy_url
            if self._config.geoip:
                kwargs["geoip"] = True
        if self._config.license_key:
            kwargs["license_key"] = self._config.license_key
        return kwargs

    def _human_scroll(self, page: Any) -> None:
        if not self._config.humanize:
            return
        for _ in range(random.randint(2, 4)):
            distance = random.randint(200, 500)
            page.mouse.wheel(0, distance)
            time.sleep(random.uniform(0.3, 0.8))

    def _is_access_restricted(self, page: Any) -> bool:
        try:
            title = (page.title() or "").lower()
            if "restricted" in title or "access denied" in title:
                return True
        except Exception:
            pass

        try:
            content = (page.content() or "").lower()
            if "access has been restricted" in content or "access denied" in content:
                return True
        except Exception:
            pass

        return False

    def _is_proxy_connection_error(self, message: str) -> bool:
        indicators = (
            "ERR_TUNNEL_CONNECTION_FAILED",
            "ERR_PROXY_CONNECTION_FAILED",
            "ERR_SOCKS_CONNECTION_FAILED",
            "net::ERR_CONNECTION",
        )
        return any(indicator in message for indicator in indicators)

    def _take_screenshot(self, page: Any, url: str) -> Path:
        slug = url.split("e=")[-1][:16].replace("&", "_")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self._config.output_dir / f"axs_{slug}_{timestamp}.png"
        page.screenshot(path=str(path), full_page=True)
        log.info("Screenshot saved: %s", path)
        return path

    def _try_add_to_cart(self, page: Any, url: str) -> bool:
        self._human_scroll(page)
        time.sleep(random.uniform(1.0, 2.0))

        selectors = [
            "button:has-text('Add to Cart')",
            "button:has-text('Buy')",
            "button:has-text('Get Tickets')",
            "a:has-text('Add to Cart')",
            "[data-testid*='add-to-cart' i]",
            "[class*='add-to-cart' i]",
            "[class*='btn-buy' i]",
        ]

        for selector in selectors:
            try:
                button = page.locator(selector).first
                button.wait_for(state="visible", timeout=3000)
                if self._config.humanize:
                    button.hover()
                    time.sleep(random.uniform(0.3, 0.6))
                button.click()
                time.sleep(random.uniform(1.5, 3.0))
                log.info("Add to Cart clicked (%s)", selector)
                return True
            except Exception:
                continue

        log.warning("No 'Add to Cart' button found on %s", url)
        return False


def setup_logging(output_dir: Path) -> None:
    log_file = output_dir / "run.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def main() -> None:
    config = Config.from_env()
    setup_logging(config.output_dir)

    log.info("Starting AXS scraping service")
    log.info("Target URLs: %s", config.target_urls)
    log.info("Action: %s", config.action)
    log.info("Headless: %s", config.headless)
    log.info("Humanize: %s", config.humanize)
    log.info("GeoIP: %s", config.geoip)
    log.info("Proxy file: %s", config.proxy_file)

    proxy_manager = ProxyManager(config.proxy_file, default_scheme=config.proxy_type)
    scraper = AXSScraper(config=config, proxy_provider=proxy_manager)

    results = scraper.run()

    summary_file = config.output_dir / "results.json"
    with open(summary_file, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)
    log.info("Summary saved to %s", summary_file)

    failed = [r for r in results if not r["success"]]
    if failed:
        log.error("%d of %d URLs failed", len(failed), len(results))
        sys.exit(1)

    log.info("Completed all %d URLs successfully", len(results))


if __name__ == "__main__":
    main()
