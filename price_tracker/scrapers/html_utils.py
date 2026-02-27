from __future__ import annotations

import re
import ssl
from urllib.request import Request, urlopen

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (price-tracker-v2; educational project)",
    "Accept-Language": "sl-SI,sl;q=0.9,en;q=0.8",
}


def fetch_html(url: str, timeout_s: int = 20, verify_ssl: bool = True) -> str:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (price-tracker; educational project)",
            "Accept-Language": "sl,en;q=0.8",
        },
    )

    ctx = None
    if not verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        # try to use certifi if installed, otherwise fallback to system CA bundle
        try:
            import certifi  # type: ignore

            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()

    with urlopen(req, timeout=timeout_s, context=ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")

def extract_title(html: str) -> str:
    # 1) og:title (atributi niso vedno v istem vrstnem redu)
    m = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            html,
            re.IGNORECASE,
        )
    if m:
        return m.group(1).strip()

    # 2) <title> fallback
    m = re.search(r"<title>\s*(.*?)\s*</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()

    return "(unknown title)"