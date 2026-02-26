from __future__ import annotations

import re
from decimal import Decimal
from typing import Tuple

from .html_utils import fetch_html


_PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€", re.IGNORECASE)


def _to_decimal_eur(s: str) -> Decimal:
    # "11,99" -> Decimal("11.99")
    return Decimal(s.replace(".", "").replace(",", "."))


def _eur_to_cents(d: Decimal) -> int:
    # safe rounding to cents
    cents = int((d * 100).quantize(Decimal("1")))
    if cents < 0:
        raise ValueError("negative price parsed")
    return cents


def _extract_title(html: str) -> str:
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


def _extract_price_cents(html: str) -> int:
    # Strategy (kot v V1):
    # najdi "Cena na enoto" blok, v naslednjih ~2000 znakih poberi vse cene,
    # main price je praviloma najnižja (unit price je višji).
    idx = html.find("Cena na enoto")
    if idx == -1:
        raise ValueError("Na strani ne najdem 'Cena na enoto' bloka (layout se je verjetno spremenil).")

    tail = html[idx : idx + 2000]
    prices = _PRICE_RE.findall(tail)
    if not prices:
        raise ValueError("Ne najdem cen v HTML (layout se je verjetno spremenil).")

    dec_prices = [_to_decimal_eur(p) for p in prices]
    main_price = min(dec_prices)
    return _eur_to_cents(main_price)


def scrape(url: str, *, verify_ssl: bool = True) -> Tuple[int, str]:
    html = fetch_html(url, verify_ssl=verify_ssl)
    title = _extract_title(html)
    price_cents = _extract_price_cents(html)
    return price_cents, title