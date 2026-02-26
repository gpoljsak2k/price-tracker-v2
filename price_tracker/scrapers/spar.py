from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Tuple
from urllib.parse import urlparse

from .html_utils import fetch_html


# SPAR search backend (JSON)
# Evidence: used in public scripts; response has hits with masterValues.best-price, title, url :contentReference[oaicite:1]{index=1}
_SPAR_SEARCH_ENDPOINT = (
    "https://search-spar.spar-ics.com/fact-finder/rest/v4/search/products_lmos_si"
)

_CODE_RE = re.compile(r"-(\d+)$")


def _eur_to_cents(d: Decimal) -> int:
    cents = int((d * 100).quantize(Decimal("1")))
    if cents < 0:
        raise ValueError("negative price parsed")
    return cents


def _to_decimal_from_any(x: Any) -> Decimal:
    if isinstance(x, (int, float)):
        return Decimal(str(x))
    s = str(x).strip()
    # "1.234,56" -> "1234.56"
    if re.match(r"^\d{1,3}(?:\.\d{3})+,\d{2}$", s):
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    return Decimal(s)


def _extract_code_from_url(product_url: str) -> str | None:
    path = urlparse(product_url).path.rstrip("/")
    last = path.split("/")[-1]  # ...-131036
    m = _CODE_RE.search(last)
    return m.group(1) if m else None


def _build_search_url(code: str) -> str:
    # common params seen in the wild: query, q, page, hitsPerPage, substringFilter=pos-visible:81701 :contentReference[oaicite:2]{index=2}
    return (
        f"{_SPAR_SEARCH_ENDPOINT}"
        f"?query={code}"
        f"&q={code}"
        f"&page=1"
        f"&hitsPerPage=10"
        f"&substringFilter=pos-visible%3A81701"
    )


def _fetch_via_search_api(product_url: str, *, verify_ssl: bool) -> Tuple[int, str]:
    code = _extract_code_from_url(product_url)
    if not code:
        raise ValueError("Ne znam izluščiti product code iz SPAR URL-ja (pričakujem ...-<digits>).")

    search_url = _build_search_url(code)

    # reuse fetch_html (headers + optional insecure ssl)
    raw = fetch_html(search_url, verify_ssl=verify_ssl)
    data = json.loads(raw)

    hits = data.get("hits") or []
    if not isinstance(hits, list) or not hits:
        raise ValueError("SPAR search API returned no hits")

    # normalize wanted url path for matching
    wanted_path = urlparse(product_url).path.rstrip("/")

    best_hit = None

    # 1) exact id match
    for h in hits:
        if not isinstance(h, dict):
            continue
        if str(h.get("id")) == code:
            best_hit = h
            break

    # 2) fallback: match masterValues.url if present
    if best_hit is None:
        for h in hits:
            mv = h.get("masterValues") if isinstance(h, dict) else None
            if not isinstance(mv, dict):
                continue
            u = mv.get("url")
            if not u:
                continue
            # script example prefixes with www.spar.si/online + url :contentReference[oaicite:3]{index=3}
            # here mv["url"] is usually a path like "/p/...."
            if str(u).rstrip("/") == wanted_path:
                best_hit = h
                break

    if best_hit is None:
        best_hit = hits[0]  # last resort

    mv = best_hit.get("masterValues")
    if not isinstance(mv, dict):
        raise ValueError("SPAR hit missing masterValues")

    price = mv.get("best-price")
    title = mv.get("title") or "(unknown title)"
    if price is None:
        raise ValueError("SPAR hit missing masterValues.best-price")

    price_dec = _to_decimal_from_any(price)
    return _eur_to_cents(price_dec), str(title)


def scrape(url: str, *, verify_ssl: bool = True) -> Tuple[int, str]:
    # primary: API (much more stable than scraping JS page)
    return _fetch_via_search_api(url, verify_ssl=verify_ssl)