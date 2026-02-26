from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class StoreItemForScrape:
    store_item_id: int
    url: str
    scraper: str
    store_name: str
    family_key: str
    canonical_label: str
    canonical_size: float
    canonical_unit: str


class StoreItemRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert_mapping(self, store_id: int, canonical_item_id: int, url: str, scraper: str) -> int:
        url = url.strip()
        scraper = scraper.strip().lower()

        if not url:
            raise ValueError("url cannot be empty")
        if not scraper:
            raise ValueError("scraper cannot be empty")

        self.conn.execute(
            """
            INSERT INTO store_item(store_id, canonical_item_id, url, scraper)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(store_id, canonical_item_id) DO UPDATE SET
              url=excluded.url,
              scraper=excluded.scraper
            """,
            (int(store_id), int(canonical_item_id), url, scraper),
        )
        row = self.conn.execute(
            "SELECT id FROM store_item WHERE store_id=? AND canonical_item_id=?",
            (int(store_id), int(canonical_item_id)),
        ).fetchone()
        if row is None:
            raise RuntimeError("failed to fetch store_item after upsert")
        return int(row["id"])

    def list_for_scrape(self) -> list[StoreItemForScrape]:
        rows = self.conn.execute(
            """
            SELECT
              si.id AS store_item_id,
              si.url,
              si.scraper,
              s.name AS store_name,
              ci.family_key AS family_key,
              ci.label AS canonical_label,
              ci.size AS canonical_size,
              ci.unit AS canonical_unit
            FROM store_item si
            JOIN store s ON s.id = si.store_id
            JOIN canonical_item ci ON ci.id = si.canonical_item_id
            ORDER BY s.name, ci.family_key, ci.unit, ci.size
            """
        ).fetchall()

        return [
            StoreItemForScrape(
                store_item_id=int(r["store_item_id"]),
                url=str(r["url"]),
                scraper=str(r["scraper"]),
                store_name=str(r["store_name"]),
                family_key=str(r["family_key"]),
                canonical_label=str(r["canonical_label"]),
                canonical_size=float(r["canonical_size"]),
                canonical_unit=str(r["canonical_unit"]),
            )
            for r in rows
        ]

    def get_by_url(self, url: str) -> dict | None:
        url = url.strip()
        if not url:
            return None
        row = self.conn.execute(
            """
            SELECT
              si.id, si.url, si.scraper,
              s.name AS store_name,
              ci.family_key AS family_key,
              ci.label AS canonical_label,
              ci.size AS canonical_size,
              ci.unit AS canonical_unit
            FROM store_item si
            JOIN store s ON s.id = si.store_id
            JOIN canonical_item ci ON ci.id = si.canonical_item_id
            WHERE si.url = ?
            """,
            (url,),
        ).fetchone()
        return dict(row) if row else None