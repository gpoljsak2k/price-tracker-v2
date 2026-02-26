from __future__ import annotations

import argparse
import datetime as dt
from typing import Tuple

from price_tracker.db import connect, init_db, transaction
from price_tracker.repos import StoreRepo, CanonicalItemRepo, StoreItemRepo, ObservationRepo


import os
# ---------- Scraper dispatch ----------

def scrape_url(scraper: str, url: str, *, verify_ssl: bool = True):
    scraper = scraper.lower().strip()
    if scraper == "mercator":
        from price_tracker.scrapers.mercator import scrape
        return scrape(url, verify_ssl=verify_ssl)
    if scraper == "hofer":
        from price_tracker.scrapers.hofer import scrape
        return scrape(url, verify_ssl=verify_ssl)
    if scraper == "lidl":
        from price_tracker.scrapers.lidl import scrape
        return scrape(url, verify_ssl=verify_ssl)
    if scraper == "spar":
        from price_tracker.scrapers.spar import scrape
        return scrape(url, verify_ssl=verify_ssl)
    raise SystemExit(f"Unknown scraper '{scraper}'. Expected: hofer | mercator | lidl | spar")


# ---------- CLI commands ----------

def cmd_init_db(args):
    conn = connect(args.db)
    init_db(conn, args.schema)
    print(f"OK: initialized db at {args.db} using {args.schema}")


def cmd_track_url(args):
    conn = connect(args.db)
    init_db(conn, args.schema)

    store_repo = StoreRepo(conn)
    canon_repo = CanonicalItemRepo(conn)
    store_item_repo = StoreItemRepo(conn)

    with transaction(conn):
        store_id = store_repo.get_or_create(args.store)
        canonical_id = canon_repo.upsert(args.key, args.label, float(args.size), args.unit)
        store_item_id = store_item_repo.upsert_mapping(
            store_id=store_id,
            canonical_item_id=canonical_id,
            url=args.url,
            scraper=args.scraper,
        )

    print(f"OK: tracked url for store_item_id={store_item_id} store={args.store} key={args.key}")

def cmd_scrape_all(args):
    conn = connect(args.db)

    import os
    print("args.db =", args.db)
    print("abs.db =", os.path.abspath(args.db))
    print("db_list =", conn.execute("PRAGMA database_list;").fetchall())
    print("store_item_count =", conn.execute("SELECT COUNT(*) FROM store_item;").fetchone()[0])

    init_db(conn, args.schema)

    store_item_repo = StoreItemRepo(conn)
    obs_repo = ObservationRepo(conn)

    observed_on = args.date or dt.date.today().isoformat()

    items = store_item_repo.list_for_scrape()
    if not items:
        print("No tracked store_items. Use track-url first.")
        return

    inserted = 0
    skipped = 0
    failed = 0

    for si in items:
        try:
            # scraper dispatcher
            price_cents, title_raw = scrape_url(
                si.scraper,
                si.url,
                verify_ssl=not args.insecure_ssl,
            )

            with transaction(conn):
                ok = obs_repo.insert_daily(
                    store_item_id=si.store_item_id,
                    observed_on=observed_on,
                    price_cents=int(price_cents),
                    title_raw=title_raw,
                )

            if ok:
                inserted += 1
                print(
                    f"[INSERT] {observed_on} "
                    f"{si.store_name} {si.canonical_key}: {price_cents}c"
                )
            else:
                skipped += 1
                print(
                    f"[SKIP]   {observed_on} "
                    f"{si.store_name} {si.canonical_key}: already observed"
                )

        except Exception as e:
            failed += 1
            print(
                f"[FAIL]   {si.store_name} {si.canonical_key} "
                f"({si.scraper}) {si.url} :: {e}"
            )

    print(
        f"Done. inserted={inserted} "
        f"skipped={skipped} failed={failed}"
    )

def compute_normalized_unit_price(price_cents: int, size: float, unit: str):
    """
    Returns (value_eur, normalized_unit) or None if not supported.
    """
    if size <= 0:
        return None

    eur = price_cents / 100

    unit = unit.lower()

    if unit == "l":
        return eur / size, "l"

    if unit == "ml":
        return eur / (size / 1000.0), "l"

    if unit == "kg":
        return eur / size, "kg"

    if unit == "g":
        return eur / (size / 1000.0), "kg"

    if unit in {"pcs", "kos"}:
        return eur / size, "pcs"

    return None

def _trend(prev_cents: int, last_cents: int):
    delta = last_cents - prev_cents
    if delta > 0:
        arrow = "↑"
    elif delta < 0:
        arrow = "↓"
    else:
        arrow = "→"
    pct = (delta / prev_cents * 100.0) if prev_cents > 0 else 0.0
    return arrow, delta, pct

def cmd_history(args):
    conn = connect(args.db)
    init_db(conn, args.schema)

    obs_repo = ObservationRepo(conn)
    canon_repo = CanonicalItemRepo(conn)

    canonical = canon_repo.get_by_key(args.key)
    if not canonical:
        print(f"Unknown canonical key: {args.key}")
        return

    size = float(canonical["size"])
    unit = str(canonical["unit"])

    # --- optional: trend summary per store (last 2 observations) ---
    if args.trend:
        rows = conn.execute(
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
            (args.key,),
        ).fetchall()

        latest2: dict[str, list[tuple[str, int]]] = {}
        for r in rows:
            store = str(r["store_name"])
            latest2.setdefault(store, [])
            if len(latest2[store]) < 2:
                latest2[store].append((str(r["observed_on"]), int(r["price_cents"])))

        print(f"Trend for {args.key} (zadnji 2 opazovanji per store)")
        print("-" * 90)
        for store, pts in latest2.items():
            if len(pts) < 2:
                if pts:
                    d1, p1 = pts[0]
                    print(f"{store:<10}  n/a (premalo podatkov)  last={p1/100:.2f} €  [{d1}]")
                else:
                    print(f"{store:<10}  n/a (premalo podatkov)")
                continue

            (d1, p1), (d2, p2) = pts[0], pts[1]  # d1 latest
            arrow, delta, pct = _trend(p2, p1)

            delta_str = f"{delta/100:+.2f} €"
            norm_str = ""
            if args.normalized:
                norm = compute_normalized_unit_price(p1, size, unit)
                if norm:
                    norm_val, norm_unit = norm
                    norm_str = f"  ({norm_val:.2f} €/{norm_unit})"

            print(f"{store:<10}  {arrow} {p1/100:>7.2f} €  {delta_str}  ({pct:+.1f}%)  [{d2}→{d1}]{norm_str}")
        print("-" * 90)

    # --- full history table ---
    points = obs_repo.history_by_canonical_key(args.key)
    if not points:
        print(f"No history for key={args.key}")
        return

    print(f"History for {args.key} (rows={len(points)})")
    print("-" * 90)

    for p in points:
        eur = p.price_cents / 100.0
        line = f"{p.observed_on}  {p.store_name:<10}  {eur:>7.2f} €"

        if args.normalized:
            norm = compute_normalized_unit_price(p.price_cents, size, unit)
            if norm:
                norm_val, norm_unit = norm
                line += f"  ({norm_val:>6.2f} €/{norm_unit})"

        if args.show_title and p.title_raw:
            line += f"  |  {p.title_raw.strip()}"

        print(line)


# ---------- argparse wiring ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="price-tracker-v2")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_db_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--db", default="price_tracker.sqlite", help="Path to sqlite db file")
        sp.add_argument("--schema", default="schema.sql", help="Path to schema.sql")

    # init-db
    p_init = sub.add_parser("init-db", help="Initialize database schema")
    add_db_args(p_init)
    p_init.set_defaults(func=cmd_init_db)

    # track-url
    p_track = sub.add_parser("track-url", help="Track a product URL and map it to a canonical item")
    add_db_args(p_track)
    p_track.add_argument("--store", required=True, help="Store name, e.g. Hofer or Mercator")
    p_track.add_argument("--url", required=True)
    p_track.add_argument("--scraper", required=True, help="Scraper key, e.g. hofer | mercator")
    p_track.add_argument("--key", required=True, help="Canonical key, e.g. milk_35_1l")
    p_track.add_argument("--label", required=True, help='Canonical label, e.g. "Mleko 3.5% 1L"')
    p_track.add_argument("--size", required=True, type=float, help="Canonical size (number), e.g. 1")
    p_track.add_argument("--unit", required=True, help="Canonical unit, e.g. l, g, kg")
    p_track.set_defaults(func=cmd_track_url)

    # scrape-all
    p_scrape = sub.add_parser("scrape-all", help="Scrape all tracked URLs and store daily price observations")
    add_db_args(p_scrape)
    p_scrape.add_argument("--date", default=None, help="Override observed_on (YYYY-MM-DD). Default: today")
    p_scrape.add_argument("--insecure-ssl", action="store_true", help="DEV ONLY: disable SSL certificate verification")
    p_scrape.set_defaults(func=cmd_scrape_all)

    # history
    p_hist = sub.add_parser("history", help="Show price history for a canonical item key")
    add_db_args(p_hist)
    p_hist.add_argument("--key", required=True, help="Canonical key, e.g. olive_oil_750ml")
    p_hist.add_argument("--show-title", action="store_true", help="Include raw scraped title in output")
    p_hist.set_defaults(func=cmd_history)
    p_hist.add_argument("--normalized", action="store_true", help="Show normalized unit price (€/l, €/kg, €/pcs)",)
    p_hist.add_argument("--trend", action="store_true", help="Show trend vs previous observation (per store)")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()