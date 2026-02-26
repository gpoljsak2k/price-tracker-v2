# price-tracker-v2

A modular CLI price tracking system built in Python.

Tracks grocery prices across multiple stores, normalizes them to unit price, supports trend analysis and automated daily scraping.

https://github.com/gpoljsak2k/price-tracker-v2/actions/workflows/ci.yml

## Features

- SQLite-backed canonical data model (Stores / Canonical Items / Store Items / Prices)
- Unit price normalization (€/kg, €/l, €/pcs)
- history – historical price tracking
    --normalized – normalized price display
    --trend – last-two-observation trend (↑ ↓ →, absolute + %)
- Multi-store support:
    - Mercator 
    - Hofer
    - Lidl
    - Spar (via backend API)
- Idempotent daily scraping
- Automated test suite (pytest)
- CI + daily scheduled scraping via GitHub Actions

## CLI overview
### Initialize database

```bash
python app.py init-db --db data/prices.sqlite --schema schema.sql
```

### Track a product URL

```bash
python app.py track-url \
  --db data/prices.sqlite \
  --schema schema.sql \
  --store Mercator \
  --scraper mercator \
  --url "PRODUCT_URL" \
  --key olive_oil_750ml \
  --label "Oljčno olje 750 ml" \
  --size 0.75 --unit l
```

### Scrape all tracked products

```bash
python app.py scrape-all \
  --db data/prices.sqlite \
  --schema schema.sql
```

### Show history

```bash
python app.py history \
  --db data/prices.sqlite \
  --schema schema.sql \
  --key olive_oil_750ml
```
With normalized unit price:
```bash
--normalized
```
With trend:
```bash
--trend
```
Example output:
```bash
Trend for olive_oil_750ml
------------------------------------------------------------------------------------------
Mercator   ↓ 11.99 €  -1.00 €  (-7.7%)  [2026-02-25→2026-02-26]
Lidl       →  5.29 €   +0.00 €  (+0.0%)
```

## Arhutecture
```bash
price_tracker/
  repos/       -> database access layer (no business logic)
  scrapers/    -> store-specific scraping logic
  services/    -> domain logic (future extensions)
  app.py       -> CLI / controller layer
  tests/       -> pytest suite
```

### Design Principles

- Canonical-based comparison (brand-independent)

- One daily observation per store item (idempotent)

- Store-specific scraping isolated per module

- Minimal, explicit SQLite schema

- Testable parsing layer (no network in tests)

## Database Model (V2)

### Core tables:

- store
- canonical_item
- store_item
- price_observation

Canonical item represents product concept.
Store item maps a store-specific URL to canonical item.
Price observation stores daily price snapshots.

## Automation

Includes:

-CI workflow (tests on push)

-Scheduled daily scraping via GitHub Actions

-Automatic commit of updated SQLite database

### Database location:
```bash
data/prices.sqlite
```

## Tech Stack

- Python 3.10
- sqlite3
- argparse
- pytest
- GitHub Actions