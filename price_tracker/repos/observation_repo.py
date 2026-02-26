from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PricePoint:
    observed_on: str          # 'YYYY-MM-DD'
    price_cents: int
    store_name: str
    family_key: str
    canonical_label: str
    canonical_size: float
    canonical_unit: str
    title_raw: Optional[str]


class ObservationRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def insert_daily(
        self,
        store_item_id: int,
        observed_on: str,
        price_cents: int,
        title_raw: Optional[str],
    ) -> bool:
        observed_on = observed_on.strip()
        if not observed_on:
            raise ValueError("observed_on cannot be empty (expected YYYY-MM-DD)")
        if int(price_cents) < 0:
            raise ValueError("price_cents must be >= 0")

        cur = self.conn.execute(
            """
            INSERT OR IGNORE INTO price_observation(store_item_id, observed_on, price_cents, title_raw)
            VALUES (?, ?, ?, ?)
            """,
            (int(store_item_id), observed_on, int(price_cents), title_raw),
        )
        return cur.rowcount == 1

    def history_by_family_key(self, family_key: str) -> list[PricePoint]:
        family_key = family_key.strip()
        if not family_key:
            raise ValueError("family_key cannot be empty")

        rows = self.conn.execute(
            """
            SELECT
              po.observed_on,
              po.price_cents,
              s.name AS store_name,
              ci.family_key AS family_key,
              ci.label AS canonical_label,
              ci.size AS canonical_size,
              ci.unit AS canonical_unit,
              po.title_raw
            FROM price_observation po
            JOIN store_item si ON si.id = po.store_item_id
            JOIN store s ON s.id = si.store_id
            JOIN canonical_item ci ON ci.id = si.canonical_item_id
            WHERE ci.family_key = ?
            ORDER BY po.observed_on ASC, s.name ASC, ci.unit ASC, ci.size ASC
            """,
            (family_key,),
        ).fetchall()

        return [
            PricePoint(
                observed_on=str(r["observed_on"]),
                price_cents=int(r["price_cents"]),
                store_name=str(r["store_name"]),
                family_key=str(r["family_key"]),
                canonical_label=str(r["canonical_label"]),
                canonical_size=float(r["canonical_size"]),
                canonical_unit=str(r["canonical_unit"]),
                title_raw=(str(r["title_raw"]) if r["title_raw"] is not None else None),
            )
            for r in rows
        ]