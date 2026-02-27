PRAGMA foreign_keys = ON;

-- =========================
-- STORE
-- =========================
CREATE TABLE IF NOT EXISTS store (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

-- =========================
-- CANONICAL ITEM (family model)
-- family_key = npr. olive_oil, milk, eggs
-- size + unit = pakiranje
-- =========================
CREATE TABLE IF NOT EXISTS canonical_item (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  family_key TEXT NOT NULL,
  label      TEXT NOT NULL,
  size       REAL NOT NULL,
  unit       TEXT NOT NULL,
  UNIQUE (family_key, size, unit)
);

CREATE INDEX IF NOT EXISTS idx_canonical_family
  ON canonical_item(family_key);

-- =========================
-- STORE ITEM (mapping URL -> canonical pack)
-- =========================
CREATE TABLE IF NOT EXISTS store_item (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id          INTEGER NOT NULL,
  canonical_item_id INTEGER NOT NULL,
  url               TEXT NOT NULL,
  scraper           TEXT NOT NULL,

  FOREIGN KEY (store_id)
    REFERENCES store(id)
    ON DELETE CASCADE,

  FOREIGN KEY (canonical_item_id)
    REFERENCES canonical_item(id)
    ON DELETE CASCADE,

  UNIQUE (store_id, canonical_item_id),
  UNIQUE (url)
);

-- =========================
-- PRICE OBSERVATION (1 zapis na dan na store_item)
-- =========================
CREATE TABLE IF NOT EXISTS price_observation (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  store_item_id INTEGER NOT NULL,
  observed_on   TEXT NOT NULL,     -- YYYY-MM-DD
  price_cents   INTEGER NOT NULL,
  title_raw     TEXT,

  FOREIGN KEY (store_item_id)
    REFERENCES store_item(id)
    ON DELETE CASCADE,

  UNIQUE (store_item_id, observed_on)
);

CREATE INDEX IF NOT EXISTS idx_obs_item_date
  ON price_observation(store_item_id, observed_on);