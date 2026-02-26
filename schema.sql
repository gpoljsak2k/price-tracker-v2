PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS store (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS canonical_item (
  id    INTEGER PRIMARY KEY AUTOINCREMENT,
  key   TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  size  REAL NOT NULL,
  unit  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS store_item (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  store_id          INTEGER NOT NULL,
  canonical_item_id INTEGER NOT NULL,
  url               TEXT NOT NULL,
  scraper           TEXT NOT NULL,

  FOREIGN KEY (store_id) REFERENCES store(id) ON DELETE CASCADE,
  FOREIGN KEY (canonical_item_id) REFERENCES canonical_item(id) ON DELETE CASCADE,

  -- ena povezava na canonical na store
  UNIQUE (store_id, canonical_item_id),

  -- URL naj bo globalno unique (da se ne podvaja)
  UNIQUE (url)
);

CREATE TABLE IF NOT EXISTS price_observation (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  store_item_id INTEGER NOT NULL,
  observed_on   TEXT NOT NULL,  -- ISO date 'YYYY-MM-DD'
  price_cents   INTEGER NOT NULL CHECK(price_cents >= 0),
  title_raw     TEXT,

  FOREIGN KEY (store_item_id) REFERENCES store_item(id) ON DELETE CASCADE,

  -- idempotent daily scrape
  UNIQUE (store_item_id, observed_on)
);

CREATE INDEX IF NOT EXISTS idx_store_item_store ON store_item(store_id);
CREATE INDEX IF NOT EXISTS idx_store_item_canonical ON store_item(canonical_item_id);
CREATE INDEX IF NOT EXISTS idx_price_obs_item_date ON price_observation(store_item_id, observed_on);
CREATE INDEX IF NOT EXISTS idx_price_obs_date ON price_observation(observed_on);