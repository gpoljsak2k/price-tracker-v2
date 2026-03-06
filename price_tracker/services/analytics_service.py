from __future__ import annotations

import sqlite3
from typing import Any

from price_tracker.utils import compute_normalized_unit_price


class AnalyticsService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -----------------
    # Families
    # -----------------
    def list_families(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT family_key FROM canonical_item ORDER BY family_key"
        ).fetchall()
        return [str(r["family_key"]) for r in rows]

    # -----------------
    # Latest prices for a family (across stores + packs)
    # -----------------
    def latest_prices(self, family_key: str) -> list[dict[str, Any]]:
        family_key = family_key.strip()
        rows = self.conn.execute(
            """
            SELECT
              s.name AS store,
              ci.size AS size,
              ci.unit AS unit,
              ci.label AS food_name,
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

        result: list[dict[str, Any]] = []

        for r in rows:
            price_cents = r["price_cents"]
            size = float(r["size"])
            unit = str(r["unit"])

            eur = None
            eur_per_unit = None
            norm_unit = None

            if price_cents is not None:
                price_cents = int(price_cents)
                eur = price_cents / 100.0
                norm = compute_normalized_unit_price(price_cents, size, unit)
                if norm:
                    eur_per_unit, norm_unit = float(norm[0]), str(norm[1])

            result.append(
                {
                    "store": str(r["store"]),
                    "food_name": str(r["food_name"]),
                    "pack": f"{size:g}{unit}",
                    "label": str(r["label"]),
                    "price_eur": eur,
                    "eur_per_unit": eur_per_unit,
                    "unit": norm_unit,
                    "date": r["observed_on"],
                }
            )

        return result

    # -----------------
    # Full history for a family
    # -----------------
    def history(self, family_key: str) -> list[dict[str, Any]]:
        family_key = family_key.strip()
        rows = self.conn.execute(
            """
            SELECT
              po.observed_on AS observed_on,
              s.name AS store,
              ci.size AS size,
              ci.unit AS unit,
              COALESCE(si.label_override, ci.label) AS label,
              po.price_cents AS price_cents
            FROM price_observation po
            JOIN store_item si ON si.id = po.store_item_id
            JOIN store s ON s.id = si.store_id
            JOIN canonical_item ci ON ci.id = si.canonical_item_id
            WHERE ci.family_key = ?
            ORDER BY po.observed_on ASC, s.name ASC, ci.unit ASC, ci.size ASC
            """,
            (family_key,),
        ).fetchall()

        out: list[dict[str, Any]] = []
        for r in rows:
            price_cents = int(r["price_cents"])
            size = float(r["size"])
            unit = str(r["unit"])

            eur = price_cents / 100.0
            norm = compute_normalized_unit_price(price_cents, size, unit)
            eur_per_unit = float(norm[0]) if norm else None
            norm_unit = str(norm[1]) if norm else None

            out.append(
                {
                    "date": str(r["observed_on"]),
                    "store": str(r["store"]),
                    "pack": f"{size:g}{unit}",
                    "price_eur": eur,
                    "eur_per_unit": eur_per_unit,
                    "unit": norm_unit,
                    "label": str(r["label"]),
                }
            )

        return out

    # -----------------
    # Compare shopping list across stores
    # shopping = {"items":[{"key":"milk","qty":2}, ...]}
    # Picks cheapest by €/unit per store per family
    # -----------------
    def compare_list(self, shopping: dict[str, Any]) -> dict[str, Any]:
        items = shopping.get("items", [])
        want: list[tuple[str, int]] = []

        for it in items:
            if not isinstance(it, dict):
                continue
            key = str(it.get("key", "")).strip()
            qty = it.get("qty", 1)
            try:
                qty = int(qty)
            except Exception:
                qty = 1
            if key and qty > 0:
                want.append((key, qty))

        stores = self.conn.execute("SELECT id, name FROM store ORDER BY name").fetchall()
        results: dict[str, Any] = {}

        for s in stores:
            sid = int(s["id"])
            sname = str(s["name"])

            total_cents = 0
            missing: list[str] = []
            chosen: list[dict[str, Any]] = []

            for family_key, qty in want:
                rows = self.conn.execute(
                    """
                    SELECT
                      ci.size AS size,
                      ci.unit AS unit,
                      COALESCE(si.label_override, ci.label) AS label,
                      si.url AS url,
                      po.observed_on AS observed_on,
                      po.price_cents AS price_cents
                    FROM store_item si
                    JOIN canonical_item ci ON ci.id = si.canonical_item_id
                    LEFT JOIN price_observation po
                      ON po.store_item_id = si.id
                     AND po.observed_on = (
                          SELECT MAX(observed_on)
                          FROM price_observation
                          WHERE store_item_id = si.id
                     )
                    WHERE si.store_id = ?
                      AND ci.family_key = ?
                    """,
                    (sid, family_key),
                ).fetchall()

                opts: list[tuple[float, str, sqlite3.Row]] = []
                for r in rows:
                    if r["price_cents"] is None:
                        continue
                    pc = int(r["price_cents"])
                    size = float(r["size"])
                    unit = str(r["unit"])
                    norm = compute_normalized_unit_price(pc, size, unit)
                    nv = float(norm[0]) if norm else (pc / 100.0)
                    nu = str(norm[1]) if norm else unit
                    opts.append((nv, nu, r))

                if not opts:
                    missing.append(f"{family_key} x{qty}")
                    continue

                nv, nu, best = min(opts, key=lambda x: x[0])
                pc = int(best["price_cents"])
                total_cents += pc * qty

                chosen.append(
                    {
                        "key": family_key,
                        "qty": qty,
                        "pack": f"{float(best['size']):g}{best['unit']}",
                        "price_eur": pc / 100.0,
                        "eur_per_unit": nv,
                        "unit": nu,
                        "date": best["observed_on"],
                        "label": best["label"],
                        "url": best["url"],
                    }
                )

            results[sname] = {
                "total_eur": total_cents / 100.0,
                "missing": missing,
                "chosen": chosen,
            }

        return results