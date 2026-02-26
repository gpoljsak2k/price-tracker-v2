from __future__ import annotations

import re
from decimal import Decimal
from typing import Tuple

from .html_utils import extract_title, fetch_html


# ujame tudi NBSP in običajne presledke: "2,54 €"
_PRICE_RE = re.compile(r"(\d{1,3}(?:\.\d{3})*,\d{2})\s*€", re.IGNORECASE)


def _to_decimal_eur(s: str) -> Decimal:
    return Decimal(s.replace(".", "").replace(",", "."))


def _eur_to_cents(d: Decimal) -> int:
    cents = int((d * 100).quantize(Decimal("1")))
    if cents < 0:
        raise ValueError("negative price parsed")
    return cents


def _extract_price_cents(html: str) -> int:
    # Primarni selector (kot v V1)
    m = re.search(
        r'class="base-price__regular"[^>]*>\s*<span>\s*([\d.,]+)\s*€\s*</span>',
        html,
        re.IGNORECASE,
    )
    if m:
        return _eur_to_cents(_to_decimal_eur(m.group(1)))

    # Fallback: poberi vse cene v HTML
    prices = _PRICE_RE.findall(html)
    if not prices:
        raise ValueError("Ne najdem cene v HTML (layout se je verjetno spremenil).")

    dec_prices = [_to_decimal_eur(p) for p in prices]

    # Hevristika iz V1: zadnja cena je pogosto main price
    return _eur_to_cents(dec_prices[-1])


def scrape(url: str, *, verify_ssl: bool = True) -> Tuple[int, str]:
    html = fetch_html(url, verify_ssl=verify_ssl)
    title = extract_title(html)
    price_cents = _extract_price_cents(html)
    return price_cents, title