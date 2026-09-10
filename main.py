import argparse
import asyncio
import sys

from src.config import settings
from src.proxy_manager import ProxyManager
from src.scraper import AXSScraper


async def test_proxies(proxy_manager: ProxyManager) -> None:
    print(f"Testing proxies from: {proxy_manager.proxy_file or proxy_manager.single_proxy}")
    print(f"Total proxies loaded: {len(proxy_manager.proxies)}")

    try:
        working = await proxy_manager.get_working_proxy()
        info = await proxy_manager.validate_proxy(working)
        print(f"✓ Active working proxy: {working.host}:{working.port}")
        print(f"✓ Sticky session: {working.session_id}")
        print(f"✓ Verified Exit IP: {info.get('ip') if info else 'Unknown'}")
    except Exception as e:
        print(f"✗ Proxy test failed: {e}", file=sys.stderr)
        sys.exit(1)


async def run_scraper(target_url: str, headless: bool) -> None:
    settings.headless = headless
    proxy_manager = ProxyManager(
        proxy_file=settings.proxy_file,
        single_proxy=settings.single_proxy,
        timeout=settings.proxy_timeout,
    )

    print(f"Starting AXS scraper for: {target_url}")
    scraper = AXSScraper(settings=settings, proxy_manager=proxy_manager)

    try:
        result = await scraper.run(target_url=target_url)
        print("Scrape completed successfully!")
        print(f"Page Title: {result.get('page_title')}")
        print(f"Final URL:  {result.get('final_url')}")
        print(f"Screenshot: {result.get('screenshot')}")
        print(f"Elapsed:    {result.get('elapsed_seconds')}s")
    except Exception as e:
        print(f"Scraper encountered an error: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="AXS Cloudflare-Protected Ticket Scraper")
    parser.add_argument("--url", type=str, default=settings.target_url, help="Target AXS URL")
    parser.add_argument("--test-proxy", action="store_true", help="Run proxy health check only")
    parser.add_argument("--no-headless", action="store_true", help="Run browser with visible GUI")

    args = parser.parse_args()

    if args.test_proxy:
        pm = ProxyManager(
            proxy_file=settings.proxy_file,
            single_proxy=settings.single_proxy,
            timeout=settings.proxy_timeout,
        )
        asyncio.run(test_proxies(pm))
    else:
        headless = not args.no_headless
        asyncio.run(run_scraper(target_url=args.url, headless=headless))


if __name__ == "__main__":
    main()
