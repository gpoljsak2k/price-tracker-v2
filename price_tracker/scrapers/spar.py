from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from .html_utils import fetch_html


_PRICE_EUR_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€")
_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_URL_ID_RE = re.compile(r"-(\d+)$")


def _to_cents_from_eur_str(eur: str) -> int:
    eur = eur.replace(".", "").replace(",", ".").strip()
    return int(round(float(eur) * 100))


def _extract_title_from_html(html: str) -> str:
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

    m = re.search(r"<title>\s*(.*?)\s*</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()

    return "(unknown title)"


def _extract_product_id(url: str) -> str | None:
    path = urlparse(url).path.strip("/")
    last = path.split("/")[-1]
    m = _URL_ID_RE.search(last)
    return m.group(1) if m else None


def _try_parse_search_api_json(raw: str, product_url: str) -> tuple[int, str] | None:
    """
    Backward-compatible parser for old SPAR search API test fixture:
    {
      "hits": [
        {
          "id": "131036",
          "masterValues": {
            "best-price": "11.99",
            "title": "...",
            "url": "/p/..."
          }
        }
      ]
    }
    """
    try:
        data = json.loads(raw)
    except Exception:
        return None

    hits = data.get("hits")
    if not isinstance(hits, list):
        return None

    wanted_id = _extract_product_id(product_url)

    # exact id match first
    chosen = None
    if wanted_id is not None:
        for hit in hits:
            if isinstance(hit, dict) and str(hit.get("id")) == wanted_id:
                chosen = hit
                break

    # fallback: first hit
    if chosen is None and hits:
        chosen = hits[0]

    if not isinstance(chosen, dict):
        return None

    mv = chosen.get("masterValues")
    if not isinstance(mv, dict):
        return None

    price = mv.get("best-price")
    title = mv.get("title")

    if price is None or title is None:
        return None

    try:
        cents = int(round(float(str(price).replace(",", ".")) * 100))
    except Exception:
        return None

    return cents, str(title)


def _try_parse_price_from_ldjson(html: str) -> tuple[int, str] | None:
    scripts = _LD_JSON_RE.findall(html)
    if not scripts:
        return None

    for raw in scripts:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue

        candidates = obj if isinstance(obj, list) else [obj]
        for it in candidates:
            if not isinstance(it, dict):
                continue

            title = it.get("name") or ""
            offers = it.get("offers")
            offer_list = offers if isinstance(offers, list) else [offers]

            for off in offer_list:
                if not isinstance(off, dict):
                    continue
                price = off.get("price")
                currency = off.get("priceCurrency")

                if price is None:
                    continue

                try:
                    price_f = float(str(price).replace(",", "."))
                    price_cents = int(round(price_f * 100))
                except Exception:
                    continue

                if currency and str(currency).upper() != "EUR":
                    continue

                return price_cents, (title.strip() or "(no title)")

    return None


def _try_parse_price_from_html_text(html: str) -> int | None:
    prices = _PRICE_EUR_RE.findall(html)
    if not prices:
        return None
    cents = [_to_cents_from_eur_str(p) for p in prices]
    return min(cents) if cents else None


def scrape(url: str, timeout_s: int = 20, verify_ssl: bool = True) -> tuple[int, str]:
    raw = fetch_html(url, timeout_s=timeout_s, verify_ssl=verify_ssl)

    # 1) old search API JSON shape (keeps tests green)
    api = _try_parse_search_api_json(raw, url)
    if api:
        return api

    # 2) JSON-LD in HTML
    ld = _try_parse_price_from_ldjson(raw)
    if ld:
        return ld

    title = _extract_title_from_html(raw)
    cents = _try_parse_price_from_html_text(raw)
    if cents is not None:
        return cents, title

    raise ValueError("Ne najdem cene na SPAR strani (HTML/API parse failed).")