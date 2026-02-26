from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PricePoint:
    observed_on: str          # 'YYYY-MM-DD'
    price_cents: int
    store_name: str
    canonical_key: str
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

    def history_by_canonical_key(self, canonical_key: str) -> list[PricePoint]:
        canonical_key = canonical_key.strip()
        if not canonical_key:
            raise ValueError("canonical_key cannot be empty")

        rows = self.conn.execute(
            """
            SELECT
              po.observed_on,
              po.price_cents,
              s.name AS store_name,
              ci.key AS canonical_key,
              po.title_raw
            FROM price_observation po
            JOIN store_item si ON si.id = po.store_item_id
            JOIN store s ON s.id = si.store_id
            JOIN canonical_item ci ON ci.id = si.canonical_item_id
            WHERE ci.key = ?
            ORDER BY po.observed_on ASC, s.name ASC
            """,
            (canonical_key,),
        ).fetchall()

        return [
            PricePoint(
                observed_on=str(r["observed_on"]),
                price_cents=int(r["price_cents"]),
                store_name=str(r["store_name"]),
                canonical_key=str(r["canonical_key"]),
                title_raw=(str(r["title_raw"]) if r["title_raw"] is not None else None),
            )
            for r in rows
        ]

    def latest_two_by_canonical_key(self, canonical_key: str) -> dict[str, list[tuple[str, int]]]:
        rows = self.conn.execute(
            """
            SELECT
              s.name AS store_name,
              po.observed_on,
              po.price_cents
            FROM price_observation po
            JOIN store_item si ON si.id = po.store_item_id
            JOIN store s ON s.id = si.store_id
            JOIN canonical_item ci ON ci.id = si.canonical_item_id
            WHERE ci.key = ?
            ORDER BY s.name ASC, po.observed_on DESC
            """,
            (canonical_key,),
        ).fetchall()

        out: dict[str, list[tuple[str, int]]] = {}
        for r in rows:
            store = str(r["store_name"])
            out.setdefault(store, [])
            if len(out[store]) < 2:
                out[store].append((str(r["observed_on"]), int(r["price_cents"])))
        return out

    def trend(prev_cents: int, last_cents: int) -> tuple[str, int, float]:
        delta = last_cents - prev_cents
        if delta > 0:
            arrow = "↑"
        elif delta < 0:
            arrow = "↓"
        else:
            arrow = "→"

        pct = 0.0
        if prev_cents > 0:
            pct = delta / prev_cents * 100.0
        return arrow, delta, pct

    def fmt_delta(delta_cents: int) -> str:
        sign = "+" if delta_cents > 0 else ""
        return f"{sign}{delta_cents / 100:.2f} €"