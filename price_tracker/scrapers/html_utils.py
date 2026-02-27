from __future__ import annotations

import re
import ssl
import certifi
import urllib.request

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (price-tracker-v2; educational project)",
    "Accept-Language": "sl-SI,sl;q=0.9,en;q=0.8",
}

def fetch_html(url: str, timeout_s: int = 20, *, verify_ssl: bool = True) -> str:
    req = urllib.request.Request(url, headers=DEFAULT_HEADERS)

    if verify_ssl:
        context = ssl.create_default_context(cafile=certifi.where())
    else:
        context = ssl._create_unverified_context()

    with urllib.request.urlopen(req, timeout=timeout_s, context=context) as resp:
        raw = resp.read()

    return raw.decode("utf-8", errors="replace")

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