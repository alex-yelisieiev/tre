import random
from typing import Dict, List


DESKTOP_USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
]

WEBGL_RENDERERS: List[Dict[str, str]] = [
    {
        "vendor": "Google Inc. (Apple)",
        "renderer": "ANGLE (Apple, Apple M3 Pro, OpenGL 4.1)",
    },
    {
        "vendor": "Google Inc. (NVIDIA)",
        "renderer": "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Direct3D11 vs_5_0 ps_5_0, D3D11)",
    },
    {
        "vendor": "Google Inc. (Intel)",
        "renderer": "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
    },
]


def get_random_user_agent() -> str:
    return random.choice(DESKTOP_USER_AGENTS)


def get_evasion_script() -> str:
    chosen_webgl = random.choice(WEBGL_RENDERERS)

    return f"""(() => {{
    // 1. Remove navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {{
        get: () => undefined,
        configurable: true
    }});

    // 2. Believable languages & platform
    Object.defineProperty(navigator, 'languages', {{
        get: () => ['en-US', 'en'],
        configurable: true
    }});

    // 3. Mock chrome runtime object
    if (!window.chrome) {{
        window.chrome = {{}};
    }}
    if (!window.chrome.runtime) {{
        window.chrome.runtime = {{
            OnInstalledReason: {{ CHROME_UPDATE: "chrome_update", INSTALL: "install", SHARED_MODULE_UPDATE: "shared_module_update", UPDATE: "update" }},
            PlatformArch: {{ ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" }},
            PlatformNaclArch: {{ ARM: "arm", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" }},
            PlatformOs: {{ ANDROID: "android", CROS: "cros", LINUX: "linux", MAC: "mac", OPENBSD: "openbsd", WIN: "win" }},
            RequestUpdateCheckStatus: {{ NO_UPDATE: "no_update", THROTTLED: "throttled", UPDATE_AVAILABLE: "update_available" }}
        }};
    }}

    // 4. Permissions API query mock
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
            Promise.resolve({{ state: Notification.permission }}) :
            originalQuery(parameters)
    );

    // 5. Spoof WebGL Vendor and Renderer (Eliminates SwiftShader in Docker/Linux)
    const getParameterProxyHandler = {{
        apply: function(target, thisArg, argumentsList) {{
            const param = argumentsList[0];
            // UNMASKED_VENDOR_WEBGL
            if (param === 37445) {{
                return "{chosen_webgl['vendor']}";
            }}
            // UNMASKED_RENDERER_WEBGL
            if (param === 37446) {{
                return "{chosen_webgl['renderer']}";
            }}
            return Reflect.apply(target, thisArg, argumentsList);
        }}
    }};

    if (window.WebGLRenderingContext) {{
        WebGLRenderingContext.prototype.getParameter = new Proxy(
            WebGLRenderingContext.prototype.getParameter,
            getParameterProxyHandler
        );
    }}
    if (window.WebGL2RenderingContext) {{
        WebGL2RenderingContext.prototype.getParameter = new Proxy(
            WebGL2RenderingContext.prototype.getParameter,
            getParameterProxyHandler
        );
    }}

    // 6. Subtle Canvas Fingerprint noise
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {{
        const context = this.getContext('2d');
        if (context) {{
            const shift = 0.0001;
            context.fillStyle = 'rgba(255, 255, 255, ' + shift + ')';
            context.fillRect(0, 0, 1, 1);
        }}
        return originalToDataURL.apply(this, arguments);
    }};

    // 7. AudioContext fingerprint randomization
    if (window.AudioBuffer) {{
        const originalGetChannelData = AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData = function() {{
            const results = originalGetChannelData.apply(this, arguments);
            for (let i = 0; i < results.length; i += 100) {{
                results[i] += 0.0000001;
            }}
            return results;
        }};
    }}
}})();"""
