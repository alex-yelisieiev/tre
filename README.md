# AXS.com Web Scraping Service

A containerised Python mini-service that navigates to AXS.com event pages,
bypasses Cloudflare protection using **CloakBrowser v0.5.10** (stealth Chromium
151 with 73 source-level C++ fingerprint patches), and saves screenshots and/or
adds tickets to the cart.

---

## How it works

| Component | Role |
|---|---|
| **CloakBrowser** | Stealth Chromium binary — `navigator.webdriver=false`, no CDP/automation signals, patched canvas/WebGL/audio/GPU/WebRTC at C++ level |
| **`humanize=True`** | Bézier mouse curves, per-character typing, realistic scroll patterns |
| **`geoip=True`** | Auto-matches timezone + locale to the proxy exit IP |
| **Xvfb** | Virtual display so CloakBrowser runs in headed mode inside Docker (maximises Cloudflare bypass) |
| **IPRoyal proxies** | US residential proxies provided in `test_data/iproyal-proxies (1).txt` |
| **Round-robin rotation** | Failed proxies are removed; next valid one is used automatically |

Cloudflare Turnstile is resolved **without any external captcha-solving service**
— purely through browser fingerprint stealth + residential proxy + human-like
behaviour.

---

## Quick start

### Docker (recommended)

```bash
# 1. Clone / enter the project
cd tre

# 2. Build and run
docker-compose up --build

# 3. Find results
ls output/
#   axs_6414022407626854_20260910T120000Z.png
#   axs_4436620017755968_20260910T120001Z.png
#   results.json
#   run.log
```

### Local (without Docker)

```bash
# Python 3.11+ required
pip install -r requirements.txt

# Copy and edit the proxy file path if needed
cp .env.example .env

# Run
python -m src.scraper
```

---

## Configuration

All settings are environment variables. Override them in `docker-compose.yml`
or a `.env` file.

| Variable | Default | Description |
|---|---|---|
| `TARGET_URLS` | Both AXS URLs | Comma-separated event page URLs |
| `PROXY_FILE` | `proxies.txt` | Path to the proxy list |
| `PROXY_TYPE` | `http` | `http`, `https`, or `socks5` |
| `ACTION` | `both` | `screenshot` / `add_to_cart` / `both` |
| `OUTPUT_DIR` | `output/` | Where screenshots + logs go |
| `HEADLESS` | `false` | `true` = headless (faster but less stealthy) |
| `HUMANIZE` | `true` | Human-like mouse / keyboard / scroll |
| `GEOIP` | `true` | Auto-detect timezone + locale from proxy IP |
| `PAGE_TIMEOUT` | `60000` | Page load timeout in ms |
| `MAX_RETRIES` | `3` | Retry attempts per URL (each with a new proxy) |
| `CLOAKBROWSER_LICENSE_KEY` | _(unset)_ | Pro key for Chromium 151 build |

### CloakBrowser license

Without a key the **free** Chromium 146 build from GitHub Releases is used.
This is enough for testing. For production or the most recent anti-detection
binary (Chromium 151), set `CLOAKBROWSER_LICENSE_KEY` in your `.env`:

```bash
CLOAKBROWSER_LICENSE_KEY=cb_your_key_here
```

A free key tied to GitHub sign-in is available at
[cloakbrowser.dev/free](https://cloakbrowser.dev/free).

---

## Proxy format

One proxy per line in `test_data/iproyal-proxies (1).txt` (mounted to
`/app/proxies.txt` in the container):

```
# IPRoyal format (auto-detected)
TEST_TASK_1:jjc9xia24jdcz421_country-us_session-RgtqsFZS_lifetime-30m@geo.iproyal.com:12321

# Generic formats also supported
user:pass@host:port
http://user:pass@host:port
socks5://user:pass@host:port
```

---

## Output

| File | Contents |
|---|---|
| `output/axs_<event_id>_<timestamp>.png` | Full-page screenshot of the loaded event page |
| `output/results.json` | Structured JSON log: URL, proxy used, success flag, screenshot path, cart status |
| `output/run.log` | Human-readable log with timestamps |

---

## Project structure

```
tre/
├── src/
│   ├── __init__.py
│   ├── config.py          # All env-var configuration
│   ├── proxy_manager.py   # Load / validate / rotate proxies
│   └── scraper.py         # Main scraper (CloakBrowser + Playwright API)
├── test_data/
│   ├── iproyal-proxies (1).txt
│   └── Python Developer (Web Scraping) Test Task (1).md
├── output/                # Created at runtime (Docker volume)
├── Dockerfile             # Multi-stage build
├── docker-compose.yml
├── docker-entrypoint.sh   # Starts Xvfb then runs python -m src.scraper
├── requirements.txt
├── .env.example
├── .gitignore
├── .dockerignore
└── README.md
```
