from __future__ import annotations

import re
from decimal import Decimal
from typing import Tuple

from .html_utils import extract_title, fetch_html


# ujame "5.29€", "5.29 €", "5,29€", tudi z zvezdico
_PRICE_RE = re.compile(r"(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})\s*€\*?", re.IGNORECASE)


def _to_decimal_eur(s: str) -> Decimal:
    # "5.29" or "5,29" -> Decimal("5.29")
    s = s.replace(".", "").replace(",", ".") if "," in s else s
    # če je format "1.234,56" -> prej odstrani tisočice
    s = s.replace(".", "") if re.match(r"^\d{1,3}(?:\.\d{3})+,\d{2}$", s) else s
    s = s.replace(",", ".")
    return Decimal(s)


def _eur_to_cents(d: Decimal) -> int:
    cents = int((d * 100).quantize(Decimal("1")))
    if cents < 0:
        raise ValueError("negative price parsed")
    return cents


def _extract_price_cents(html: str) -> int:
    prices = _PRICE_RE.findall(html)
    if not prices:
        raise ValueError("Ne najdem cene v HTML (layout se je verjetno spremenil).")

    dec_prices = [_to_decimal_eur(p) for p in prices]

    # Lidl pogosto prikaže staro (višjo) in novo (nižjo) ceno -> vzamemo minimum.
    return _eur_to_cents(min(dec_prices))


def scrape(url: str, *, verify_ssl: bool = True) -> Tuple[int, str]:
    html = fetch_html(url, verify_ssl=verify_ssl)
    title = extract_title(html)
    price_cents = _extract_price_cents(html)
    return price_cents, title