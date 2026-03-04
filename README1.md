# Price Tracker V2

Price Tracker V2 is a personal tool for tracking grocery prices across
stores and comparing shopping baskets.

It collects prices from online store pages, stores them in a SQLite
database, and provides a **Streamlit UI** to explore prices, trends, and
optimize a shopping list.

Main features:

-   Track products from different stores
-   Automatically scrape daily prices
-   Compare prices across stores
-   Visualize price history
-   Optimize a shopping list
-   Export results to CSV / JSON

------------------------------------------------------------------------

# Features

## Latest prices

Shows the latest observed prices for a selected product family.

Displays:

-   store
-   food name (canonical product name)
-   brand / label
-   pack size
-   price
-   €/unit
-   date of observation

Highlights the **cheapest option**.

------------------------------------------------------------------------

## Price history

Shows historical price trends.

Features:

-   price trend chart
-   normalized price (€/unit)
-   raw history table
-   filtering by store / product

Useful for identifying price changes over time.

------------------------------------------------------------------------

## Shopping list optimizer

Build a shopping basket and compare stores.

### Builder

You can add items directly in the UI:

-   choose product
-   choose quantity
-   add to basket

The basket is editable.

### Comparison modes

**Single store**

Find the cheapest store that contains the whole basket.

**Split basket**

Find the cheapest store for each item individually.

Example:

Milk → Lidl\
Olive oil → Mercator

**Split + penalty**

Allows adding a cost for visiting additional stores.

Example:

Penalty per store: 2€

This helps simulate time / travel cost.

### Export

You can export:

-   shopping_list.json
-   basket.csv
-   compare_single_STORE.csv
-   compare_split.csv

------------------------------------------------------------------------

## Add items

Add new tracked products directly from the UI.

Required fields:

store\
scraper\
url\
key (family)\
food name\
size\
unit\
optional store label

Example:

Store: Spar\
Key: milk_35\
Food name: Trajno polnomastno mleko\
Size: 1\
Unit: l

------------------------------------------------------------------------

## Scraping

Prices are collected using scrapers defined in the project.

Command line:

python app.py scrape-all

This:

-   loads all tracked URLs
-   scrapes the page
-   stores price observations

Each store item stores **one observation per day**.

------------------------------------------------------------------------

## Sync with GitHub

The project stores the SQLite database in the repository.

To update your local DB:

python app.py sync

If local changes block pull:

python app.py sync --discard-db

------------------------------------------------------------------------

# Installation

Clone the repository:

git clone https://github.com/YOURNAME/price-tracker-v2.git\
cd price-tracker-v2

Create virtual environment:

python -m venv .venv\
source .venv/bin/activate

Install dependencies:

pip install -r requirements.txt

------------------------------------------------------------------------

# Run the UI

Start Streamlit:

streamlit run ui/streamlit_app.py

Then open:

http://localhost:8501

------------------------------------------------------------------------

# Project structure

price-tracker-v2 │ ├── app.py ├── schema.sql ├── data/ │ └──
prices.sqlite │ ├── price_tracker/ │ ├── services/ │ │ └──
analytics_service.py │ ├── scrapers/ │ └── repos/ │ ├── ui/ │ └──
streamlit_app.py │ └── README.md

------------------------------------------------------------------------

# Database

Main tables:

### store

List of stores.

### canonical_item

Defines the **product family and pack**.

Example:

olive_oil\
0.75 l

### store_item

Maps store URLs to canonical items.

store\
canonical item\
scraper\
url

### price_observation

Stores daily price observations.

store_item_id\
observed_on\
price_cents

------------------------------------------------------------------------

# Example workflow

1.  Add product URL

python app.py track-url\
--store Spar\
--scraper spar\
--url https://example\
--key milk_35\
--label "Trajno mleko 3.5%"\
--size 1\
--unit l

2.  Scrape prices

python app.py scrape-all

3.  Open UI

streamlit run ui/streamlit_app.py

4.  Build shopping list and compare stores.

------------------------------------------------------------------------

# Future improvements

Possible improvements:

-   automatic scheduled scraping
-   price alerts
-   store promotions detection
-   barcode scanning
-   mobile friendly UI
-   multi-country store support

------------------------------------------------------------------------

# License

MIT License
