from __future__ import annotations

import json
import re

from .html_utils import fetch_html


_PRICE_EUR_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€")
_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


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
    html = fetch_html(url, timeout_s=timeout_s, verify_ssl=verify_ssl)

    ld = _try_parse_price_from_ldjson(html)
    if ld:
        return ld

    title = _extract_title_from_html(html)
    cents = _try_parse_price_from_html_text(html)
    if cents is not None:
        return cents, title

    raise ValueError("Ne najdem cene na SPAR strani (HTML parse failed).")