from __future__ import annotations

import sqlite3
from typing import List, Dict, Any
from price_tracker.utils import compute_normalized_unit_price


class AnalyticsService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def list_families(self) -> List[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT family_key FROM canonical_item ORDER BY family_key"
        ).fetchall()
        return [r["family_key"] for r in rows]

    def latest_prices(self, family_key: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT
              s.name AS store,
              ci.size AS size,
              ci.unit AS unit,
              COALESCE(si.label_override, ci.label) AS label,
              po.observed_on,
              po.price_cents
            FROM store_item si
            JOIN store s ON s.id = si.store_id
            JOIN canonical_item ci ON ci.id = si.canonical_item_id
            LEFT JOIN price_observation po
              ON po.store_item_id = si.id
             AND po.observed_on = (
                  SELECT MAX(observed_on)
                  FROM price_observation
                  WHERE store_item_id = si.id
             )
            WHERE ci.family_key = ?
            ORDER BY s.name, ci.unit, ci.size
            """,
            (family_key,),
        ).fetchall()

        result = []

        for r in rows:
            price_cents = r["price_cents"]
            size = float(r["size"])
            unit = str(r["unit"])

            eur = None
            eur_per_unit = None
            norm_unit = None

            if price_cents is not None:
                eur = price_cents / 100.0
                norm = compute_normalized_unit_price(price_cents, size, unit)
                if norm:
                    eur_per_unit, norm_unit = norm

            result.append(
                {
                    "store": r["store"],
                    "pack": f"{size:g}{unit}",
                    "label": r["label"],
                    "price_eur": eur,
                    "eur_per_unit": eur_per_unit,
                    "unit": norm_unit,
                    "date": r["observed_on"],
                }
            )

        return result