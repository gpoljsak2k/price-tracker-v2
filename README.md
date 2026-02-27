# Price Tracker V2

CLI price tracking tool for Slovenian grocery stores (Hofer, Mercator, Lidl, Spar).

Tracks product prices daily, normalizes by unit (€/l, €/kg, €/pcs), 
and compares shopping lists across stores.

https://github.com/gpoljsak2k/price-tracker-v2/actions/workflows/ci.yml

## Features

- Track product URLs from multiple stores
- Canonical "family_key" model (e.g. olive_oil, milk)
- Automatic daily scraping via GitHub Actions
- Price history with trend indicator
  - --normalized – normalized price display
  - --trend – last-two-observation trend (↑ ↓ →, absolute + %)
- Unit-normalized comparison (€/l, €/kg, €/pcs)
- Shopping list comparison across stores
- Healthcheck (`doctor`) for scraper stability
- Automated test suite (pytest)

## Architecture

- Python 3.10
- SQLite
- argparse CLI
- Daily scraping via GitHub Actions
- Canonical family-based product model

### Core Tables

- store
- canonical_item (family_key + size + unit)
- store_item (URL mapping)
- price_observation (1 per day per item)

## Setup

```bash
git clone https://github.com/...
cd price-tracker-v2

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
## Initialize database

```bash
python app.py init-db --db data/prices.sqlite --schema schema.sql
```
## Track a product

```bash
python app.py track-url \
  --db data/prices.sqlite \
  --schema schema.sql \
  --store Mercator \
  --scraper mercator \
  --url "https://..." \
  --key olive_oil \
  --label "Monini Classico" \
  --size 0.75 \
  --unit l
```
- `--key` = family key (olive_oil, milk, eggs)
- `--size` + `--unit` = pack size

## Scrape prices

Run manually:

```bash
python app.py scrape-all --db data/prices.sqlite --schema schema.sql

# CI friendly
python app.py scrape-all --db data/prices.sqlite --schema schema.sql --fail-on-error
```
## Automation

Daily scraping runs via GitHub Actions at 06:10 UTC.

The workflow:
- Runs `scrape-all`
- Commits updated `data/prices.sqlite` if changed

## sync Command

The sync command keeps your local database up to date with the latest prices collected by GitHub Actions.

- What sync does:
    - Runs a fast-forward git pull
    - Optionally shows the latest observed_on date in the database

- This ensures your local data/prices.sqlite contains the newest price observations.

### Usage
```bash
python app.py sync --db data/prices.sqlite --schema schema.sql
```
#### Show latest available data after syncing:
```bash
python app.py sync --db data/prices.sqlite --schema schema.sql --show-db
```

## Scraper healthcheck

```bash
python app.py doctor --db data/prices.sqlite --schema schema.sql --fail-on-error
```

## List tracked items

```bash
python app.py list-tracked --db data/prices.sqlite --schema schema.sql --normalized
```
## Shopping list

Create/edit list:

```bash
python app.py list-add --list shopping_list.json --key olive_oil --qty 1
python app.py list-add --list shopping_list.json --key milk --qty 2
```
### Compare list
```bash
python app.py compare-list \
  --db data/prices.sqlite \
  --schema schema.sql \
  --list shopping_list.json
```
## Design Decisions

- Family-based canonical model instead of strict SKU matching
- One price observation per day per store item (idempotent scraping)
- Normalized unit price comparison for cross-pack evaluation
- SQLite committed to repo for reproducible history

## Tech Stack

- Python 3.10
- sqlite3
- argparse
- pytest
- GitHub Actions

## Limitations

- Scrapers may break if store HTML changes
- SQLite DB grows over time
- No web UI (CLI only) (for now)

## Roadmap

- Web UI (Streamlit or FastAPI)
- Price drop alerts
- Historical charts
- Export to CSV
- DB pruning strategy
