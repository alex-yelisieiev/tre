"""
AXS.com scraper — main entry point.

Uses CloakBrowser (stealth Chromium 151 with 73 C++ fingerprint patches) to:
  1. Navigate to AXS event pages
  2. Bypass Cloudflare Turnstile natively (no external captcha solvers)
  3. Take a screenshot and/or attempt to add a ticket to the cart
  4. Save results to the output/ directory

Run directly:
    python -m src.scraper

Or via Docker:
    docker-compose up
"""
from __future__ import annotations

import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# CF wait timeout: how long to poll for the challenge to clear.
# 30s is often not enough on the free binary; 90s gives CF more room.
_CF_WAIT_TIMEOUT = int(os.getenv("CF_WAIT_TIMEOUT", "90")) * 1000  # convert to ms

from . import config
from .proxy_manager import ProxyManager

# ── Logging setup ─────────────────────────────────────────────────────────────
_LOG_FILE = config.OUTPUT_DIR / "run.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger("axs_scraper")


# ── Human-like helpers ────────────────────────────────────────────────────────

def _sleep(lo: float = 0.5, hi: float = 2.5) -> None:
    time.sleep(random.uniform(lo, hi))


def _human_scroll(page) -> None:
    """Scroll down a bit in a human-like pattern."""
    for _ in range(random.randint(2, 5)):
        distance = random.randint(200, 600)
        page.mouse.wheel(0, distance)
        _sleep(0.3, 0.9)


# ── Cloudflare detection ──────────────────────────────────────────────────────

def _cloudflare_present(page) -> bool:
    """Return True if a Cloudflare challenge is still active on the page."""
    try:
        # Cloudflare challenge iframes carry a title containing "challenge"
        cf_frame = page.frame_locator("iframe[title*='challenge' i]")
        # A very short timeout — we just want to know if it's there
        cf_frame.locator("body").wait_for(timeout=500)
        return True
    except Exception:
        pass

    # Also check the page title
    try:
        title = page.title()
        return "just a moment" in title.lower() or "checking your browser" in title.lower()
    except Exception:
        return False


def _is_access_restricted(page) -> bool:
    """Return True if AXS/Akamai rendered an access restricted / IP block page."""
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


def _try_click_turnstile(page) -> bool:
    """
    Click the Cloudflare Turnstile checkbox using multiple strategies.

    Strategy A: click the Turnstile iframe element on the main page — the
                checkbox is visually centered in the iframe, so clicking the
                element lands on it.
    Strategy B: pierce into the frame via frame_locator and try known selectors.
    Strategy C: use page.frames to find the CF frame and run JS inside it to
                click specific checkbox elements.
    """
    # ── Strategy A: click the iframe element on the host/challenge page ────────
    host_iframe_selectors = [
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[title*='Widget containing a Cloudflare' i]",
        "iframe[title*='Widget' i]",
        "iframe[title*='challenge' i]",
    ]
    for sel in host_iframe_selectors:
        try:
            iframe_el = page.locator(sel).first
            # Use a longer timeout — CF may render the iframe after a delay
            iframe_el.wait_for(state="visible", timeout=4_000)
            bbox = iframe_el.bounding_box()
            if bbox:
                if config.HUMANIZE:
                    iframe_el.hover()
                    _sleep(0.2, 0.5)
                # Click slightly left-of-center to hit the checkbox (it's on the left)
                page.mouse.click(
                    bbox["x"] + min(24, bbox["width"] * 0.15),
                    bbox["y"] + bbox["height"] / 2,
                )
                log.info("Clicked Turnstile checkbox via bounding-box click: %s", sel)
                return True
        except Exception as exc:
            log.debug("Strategy A failed (%s): %s", sel, exc)
            continue

    # ── Strategy B: pierce into frame via frame_locator ───────────────────────
    for iframe_sel in host_iframe_selectors:
        try:
            cf_frame = page.frame_locator(iframe_sel)
            for inner in (
                "[role='checkbox']",
                ".ctp-checkbox-label",
                ".cb-lb",
                "input[type='checkbox']",
                "span[class*='checkbox' i]",
                "label",
                "body",
            ):
                try:
                    loc = cf_frame.locator(inner).first
                    loc.wait_for(state="visible", timeout=1_500)
                    if config.HUMANIZE:
                        loc.hover()
                        _sleep(0.2, 0.4)
                    loc.click()
                    log.info("Clicked Turnstile inner element (%s > %s)", iframe_sel, inner)
                    return True
                except Exception:
                    continue
        except Exception:
            continue

    # ── Strategy C: JS eval inside the CF frame with specific selectors ────────
    try:
        cf_frames = [f for f in page.frames
                     if f.url and "challenges.cloudflare.com" in f.url]
        if cf_frames:
            cf_frame = cf_frames[0]
            result = cf_frame.evaluate("""() => {
                const candidates = [
                    'input[type="checkbox"]',
                    '[role="checkbox"]',
                    '.ctp-checkbox-label',
                    '.cb-lb',
                    '#checkbox',
                    'label',
                ];
                for (const sel of candidates) {
                    const el = document.querySelector(sel);
                    if (el) { el.click(); return sel; }
                }
                // Last resort: click body
                document.body.click();
                return 'body';
            }""")
            log.info("Strategy C JS click fired — hit element: %s", result)
            return True
    except Exception as exc:
        log.debug("Strategy C failed: %s", exc)

    log.debug("All Turnstile click strategies failed — CF may still be processing")
    return False


def _wait_for_cf(page, max_wait: int = _CF_WAIT_TIMEOUT) -> bool:
    """
    Wait up to *max_wait* ms for Cloudflare to clear, actively clicking the
    Turnstile checkbox every few seconds if it's visible.

    Returns True if the page cleared, False if still blocked.
    """
    deadline = time.monotonic() + max_wait / 1000
    elapsed = 0
    click_interval = 5  # try clicking every N seconds
    next_click_at = 2   # give the page 2s to render before first click attempt

    while time.monotonic() < deadline:
        if not _cloudflare_present(page):
            log.info("Cloudflare challenge cleared ✓  (after ~%ds)", elapsed)
            return True

        if elapsed % 10 == 0:
            log.info("Cloudflare challenge active, waiting … (%ds elapsed)", elapsed)

        # Actively try to click the Turnstile checkbox
        if elapsed >= next_click_at:
            clicked = _try_click_turnstile(page)
            if clicked:
                log.info("Turnstile click sent at %ds — waiting for resolution …", elapsed)
                _sleep(2.0, 4.0)  # give CF time to process the click
                elapsed += 3
                next_click_at = elapsed + click_interval
                continue

        time.sleep(1.0)
        elapsed += 1

    log.warning("Cloudflare challenge did NOT clear within %ds", max_wait // 1000)
    return False


# ── Cart action ───────────────────────────────────────────────────────────────

def _try_add_to_cart(page, url: str) -> bool:
    """
    Attempt to add a ticket to the cart.

    AXS shop pages present ticket sections and "Add to Cart" buttons.
    We look for any visible button that looks like a purchase CTA.
    """
    _human_scroll(page)
    _sleep(1.0, 2.0)

    # Candidate selectors (most to least specific)
    selectors = [
        "button:has-text('Add to Cart')",
        "button:has-text('Buy')",
        "button:has-text('Get Tickets')",
        "a:has-text('Add to Cart')",
        "[data-testid*='add-to-cart' i]",
        "[class*='add-to-cart' i]",
        "[class*='btn-buy' i]",
    ]

    for sel in selectors:
        try:
            btn = page.locator(sel).first
            btn.wait_for(state="visible", timeout=3_000)
            log.info("Found cart button (%s) — clicking", sel)
            if config.HUMANIZE:
                btn.hover()
                _sleep(0.3, 0.8)
            btn.click()
            _sleep(1.5, 3.0)
            log.info("Cart button clicked ✓")
            return True
        except Exception:
            continue

    log.warning("No 'Add to Cart' button found on %s", url)
    return False


# ── Screenshot ────────────────────────────────────────────────────────────────

def _take_screenshot(page, url: str) -> Path:
    slug = url.split("e=")[-1][:16].replace("&", "_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = config.OUTPUT_DIR / f"axs_{slug}_{ts}.png"
    page.screenshot(path=str(path), full_page=True)
    log.info("Screenshot saved → %s", path)
    return path


# ── Core scrape loop ──────────────────────────────────────────────────────────

def _build_launch_kwargs(proxy_url: Optional[str]) -> dict:
    kwargs: dict = {
        "headless": config.HEADLESS,
        "humanize": config.HUMANIZE,
        "human_preset": "careful",  # recommended by CloakBrowser for tough sites
    }

    if proxy_url:
        kwargs["proxy"] = proxy_url
        if config.GEOIP:
            kwargs["geoip"] = True

    if config.LICENSE_KEY:
        kwargs["license_key"] = config.LICENSE_KEY

    return kwargs


def scrape_url(url: str, proxy_manager: ProxyManager) -> dict:
    """
    Scrape a single AXS event URL.

    Returns a result dict with keys: url, proxy, success, screenshot, cart, error.
    """
    from cloakbrowser import launch

    for attempt in range(1, config.MAX_RETRIES + 1):
        proxy = proxy_manager.get_next()
        log.info("Attempt %d/%d  url=%s  proxy=%s", attempt, config.MAX_RETRIES, url, proxy)

        browser = None
        try:
            launch_kwargs = _build_launch_kwargs(proxy)
            browser = launch(**launch_kwargs)
            page = browser.new_page()

            # Set a generous default timeout
            page.set_default_timeout(config.PAGE_TIMEOUT)

            # Navigate
            page.goto(url, wait_until="domcontentloaded", timeout=config.PAGE_TIMEOUT)
            _sleep(1.5, 3.0)

            # Wait for Cloudflare to resolve
            cf_cleared = _wait_for_cf(page)
            if not cf_cleared:
                # Save a debug screenshot so we can see what CF is showing
                _take_screenshot(page, url + "_cf_blocked")
                # NOTE: do NOT mark_failed here — the proxy IS working,
                # it's just Cloudflare that didn't clear. Rotate proxy anyway
                # so the next attempt uses a fresh session/IP.
                log.info("CF not cleared — rotating proxy for next attempt")
                continue

            # Wait for the page to fully settle
            try:
                page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception:
                pass  # networkidle can time out on SPAs; proceed anyway

            # Detect AXS / Akamai IP ban
            if _is_access_restricted(page):
                log.warning("AXS access restricted (IP flagged by Akamai) on proxy %s — rotating proxy for next attempt", proxy)
                _take_screenshot(page, url + "_restricted")
                if proxy:
                    proxy_manager.mark_failed(proxy)
                continue

            _human_scroll(page)
            _sleep(0.5, 1.5)

            result: dict = {
                "url": url,
                "proxy": proxy,
                "attempt": attempt,
                "success": True,
                "screenshot": None,
                "cart_added": False,
                "error": None,
            }

            # Screenshot
            if config.ACTION in ("screenshot", "both"):
                shot_path = _take_screenshot(page, url)
                result["screenshot"] = str(shot_path)

            # Add to cart
            if config.ACTION in ("add_to_cart", "both"):
                result["cart_added"] = _try_add_to_cart(page, url)
                # Take a post-action screenshot regardless
                if config.ACTION == "add_to_cart":
                    shot_path = _take_screenshot(page, url)
                    result["screenshot"] = str(shot_path)

            return result

        except Exception as exc:
            err_str = str(exc)
            log.error("Attempt %d failed: %s", attempt, exc, exc_info=True)
            # Only penalise the proxy for genuine connection errors.
            # CF challenge failures are not the proxy's fault.
            if proxy and any(e in err_str for e in (
                "ERR_TUNNEL_CONNECTION_FAILED",
                "ERR_PROXY_CONNECTION_FAILED",
                "ERR_SOCKS_CONNECTION_FAILED",
                "net::ERR_CONNECTION",
            )):
                proxy_manager.mark_failed(proxy)

        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

    return {
        "url": url,
        "proxy": None,
        "attempt": config.MAX_RETRIES,
        "success": False,
        "screenshot": None,
        "cart_added": False,
        "error": f"All {config.MAX_RETRIES} attempts failed",
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    log.info("=== AXS.com scraper starting ===")
    log.info("Target URLs : %s", config.TARGET_URLS)
    log.info("Action      : %s", config.ACTION)
    log.info("Headless    : %s", config.HEADLESS)
    log.info("Humanize    : %s", config.HUMANIZE)
    log.info("GeoIP       : %s", config.GEOIP)
    log.info("Proxy file  : %s", config.PROXY_FILE)
    log.info("Output dir  : %s", config.OUTPUT_DIR)
    log.info("License key : %s", "set" if config.LICENSE_KEY else "not set (free build)")

    proxy_manager = ProxyManager(config.PROXY_FILE, default_scheme=config.PROXY_TYPE)
    log.info("Loaded %d proxies", proxy_manager.count)

    results = []
    for url in config.TARGET_URLS:
        result = scrape_url(url, proxy_manager)
        results.append(result)
        status = "✓ SUCCESS" if result["success"] else "✗ FAILED"
        log.info("%s  %s", status, url)

    # Save structured JSON log
    summary_path = config.OUTPUT_DIR / "results.json"
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)
    log.info("Results saved → %s", summary_path)

    failed = [r for r in results if not r["success"]]
    if failed:
        log.error("%d / %d URLs failed", len(failed), len(results))
        sys.exit(1)
    else:
        log.info("All %d URLs scraped successfully 🎉", len(results))


if __name__ == "__main__":
    main()
