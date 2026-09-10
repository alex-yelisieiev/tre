import random
import re
import string
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
import httpx


@dataclass
class ProxyInfo:
    protocol: str
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    session_id: Optional[str] = None

    @property
    def http_url(self) -> str:
        if self.username and self.password:
            return f"{self.protocol}://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"{self.protocol}://{self.host}:{self.port}"

    @property
    def server_address(self) -> str:
        return f"{self.host}:{self.port}"


class ProxyManager:
    def __init__(
        self,
        proxy_file: Optional[Path] = None,
        single_proxy: Optional[str] = None,
        timeout: float = 12.0,
        validation_url: str = "https://api.ipify.org?format=json",
    ):
        self.proxy_file = proxy_file
        self.single_proxy = single_proxy
        self.timeout = timeout
        self.validation_url = validation_url
        self.proxies: List[ProxyInfo] = []
        self.current_index: int = 0

        self._load_proxies()

    def _load_proxies(self) -> None:
        if self.single_proxy:
            parsed = self._parse_proxy_string(self.single_proxy)
            if parsed:
                self.proxies.append(parsed)
            return

        if self.proxy_file and self.proxy_file.exists():
            with open(self.proxy_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parsed = self._parse_proxy_string(line)
                    if parsed:
                        self.proxies.append(parsed)

    @staticmethod
    def _parse_proxy_string(raw: str) -> Optional[ProxyInfo]:
        protocol = "http"
        if "://" in raw:
            protocol, raw = raw.split("://", 1)
            protocol = protocol.lower()

        # Format: username:password@host:port
        auth_match = re.match(r"^([^:]+):([^@]+)@([^:]+):(\d+)$", raw)
        if auth_match:
            user, password, host, port = auth_match.groups()
            session_match = re.search(r"session-([A-Za-z0-9]+)", password)
            session_id = session_match.group(1) if session_match else None
            return ProxyInfo(
                protocol=protocol,
                host=host,
                port=int(port),
                username=user,
                password=password,
                session_id=session_id,
            )

        # Format: host:port:username:password
        hpupp_match = re.match(r"^([^:]+):(\d+):([^:]+):(.+)$", raw)
        if hpupp_match:
            host, port, user, password = hpupp_match.groups()
            return ProxyInfo(
                protocol=protocol,
                host=host,
                port=int(port),
                username=user,
                password=password,
            )

        # Format: host:port
        hp_match = re.match(r"^([^:]+):(\d+)$", raw)
        if hp_match:
            host, port = hp_match.groups()
            return ProxyInfo(
                protocol=protocol,
                host=host,
                port=int(port),
            )

        return None

    def renew_sticky_session(self, proxy: ProxyInfo) -> ProxyInfo:
        if not proxy.password or "session-" not in proxy.password:
            return proxy

        chars = string.ascii_letters + string.digits
        new_session = "".join(random.choices(chars, k=8))
        new_password = re.sub(
            r"session-[A-Za-z0-9]+", f"session-{new_session}", proxy.password
        )
        return ProxyInfo(
            protocol=proxy.protocol,
            host=proxy.host,
            port=proxy.port,
            username=proxy.username,
            password=new_password,
            session_id=new_session,
        )

    async def validate_proxy(self, proxy: ProxyInfo) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(
                proxy=proxy.http_url, timeout=self.timeout
            ) as client:
                resp = await client.get(self.validation_url)
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "ip": data.get("ip"),
                        "session": proxy.session_id,
                        "valid": True,
                    }
        except Exception:
            return None
        return None

    async def get_working_proxy(self) -> ProxyInfo:
        if not self.proxies:
            raise RuntimeError("No proxies available in configuration")

        attempts = min(len(self.proxies) * 2, 10)
        for _ in range(attempts):
            proxy = self.proxies[self.current_index % len(self.proxies)]
            self.current_index += 1

            result = await self.validate_proxy(proxy)
            if result:
                return proxy

            # If failed, try regenerating session for sticky proxies
            if proxy.session_id:
                refreshed = self.renew_sticky_session(proxy)
                result = await self.validate_proxy(refreshed)
                if result:
                    return refreshed

        raise RuntimeError("Failed to find a responsive proxy after multiple checks")

    @staticmethod
    def create_auth_extension(proxy: ProxyInfo) -> Optional[Path]:
        if not (proxy.username and proxy.password):
            return None

        ext_dir = Path(tempfile.mkdtemp(prefix="proxy_auth_ext_"))
        manifest_path = ext_dir / "manifest.json"
        background_path = ext_dir / "background.js"

        manifest_content = """{
    "version": "1.0.0",
    "manifest_version": 2,
    "name": "Chrome Proxy Authentication",
    "permissions": [
        "proxy",
        "tabs",
        "unlimitedStorage",
        "*://*/*",
        "<all_urls>",
        "webRequest",
        "webRequestBlocking"
    ],
    "background": {
        "scripts": ["background.js"]
    },
    "minimum_chrome_version": "76.0.0"
}"""
        background_content = f"""
var config = {{
    mode: "fixed_servers",
    rules: {{
        singleProxy: {{
            scheme: "{proxy.protocol}",
            host: "{proxy.host}",
            port: parseInt({proxy.port})
        }},
        bypassList: ["localhost", "127.0.0.1"]
    }}
}};
chrome.proxy.settings.set({{value: config, scope: "regular"}}, function() {{}});
function callbackFn(details) {{
    return {{
        authCredentials: {{
            username: "{proxy.username}",
            password: "{proxy.password}"
        }}
    }};
}}
chrome.webRequest.onAuthRequired.addListener(
    callbackFn,
    {{urls: ["<all_urls>"]}},
    ['blocking']
);
"""
        manifest_path.write_text(manifest_content, encoding="utf-8")
        background_path.write_text(background_content, encoding="utf-8")
        return ext_dir
