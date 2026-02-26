import json
import pytest


def test_mercator_extract_title_and_price():
    # import inside test so pytest can import package cleanly
    from price_tracker.scrapers import mercator as m

    html = """
    <html>
      <head>
        <meta property="og:title" content="Monini oljčno olje 750 ml"/>
        <title>Fallback title</title>
      </head>
      <body>
        <div>... nekaj ...</div>
        <div>Cena na enoto</div>
        <div>15,99 € / 1l</div>
        <div>11,99 €</div>
      </body>
    </html>
    """

    assert m._extract_title(html) == "Monini oljčno olje 750 ml"
    assert m._extract_price_cents(html) == 1199


def test_hofer_extract_title_and_price_primary_selector():
    from price_tracker.scrapers import hofer as h
    from price_tracker.scrapers.html_utils import extract_title

    html = """
    <html>
      <head>
        <meta property="og:title" content="Hofer extra deviško oljčno olje"/>
      </head>
      <body>
        <div class="base-price__regular">
          <span> 7,49 € </span>
        </div>
      </body>
    </html>
    """

    assert extract_title(html) == "Hofer extra deviško oljčno olje"
    assert h._extract_price_cents(html) == 749


def test_lidl_extract_price_min_of_multiple_prices():
    from price_tracker.scrapers import lidl as l

    html = """
    <html>
      <head><meta property="og:title" content="Lidl oljčno olje"/></head>
      <body>
        <div>Stara cena: 6.49 €</div>
        <div>Nova cena: 5.29 €*</div>
      </body>
    </html>
    """

    assert l._extract_price_cents(html) == 529


def test_spar_scrape_via_search_api(monkeypatch):
    from price_tracker.scrapers import spar as s

    product_url = "https://online.spar.si/p/ekstra-devisko-oljcno-olje-classico-monini-750ml-131036"

    fake = {
        "hits": [
            {
                "id": "131036",
                "masterValues": {
                    "best-price": "11.99",
                    "title": "Ekstra deviško oljčno olje Classico, Monini, 750 ml",
                    "url": "/p/ekstra-devisko-oljcno-olje-classico-monini-750ml-131036",
                },
            }
        ]
    }

    def fake_fetch_html(url: str, *args, **kwargs) -> str:
        return json.dumps(fake)

    # spar.py uses `from .html_utils import fetch_html` => patch module-local name
    monkeypatch.setattr(s, "fetch_html", fake_fetch_html)

    price_cents, title = s.scrape(product_url, verify_ssl=True)
    assert price_cents == 1199
    assert "Monini" in title