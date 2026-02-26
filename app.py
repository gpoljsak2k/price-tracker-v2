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

        # args.key == family_key
        canonical_id = canon_repo.upsert(
            args.key,
            args.label,
            float(args.size),
            args.unit,
        )

        store_item_id = store_item_repo.upsert_mapping(
            store_id=store_id,
            canonical_item_id=canonical_id,
            url=args.url,
            scraper=args.scraper,
        )

    print(
        f"OK: tracked url for store_item_id={store_item_id} "
        f"store={args.store} family={args.key} size={args.size}{args.unit}"
    )

def cmd_scrape_all(args):
    conn = connect(args.db)
    init_db(conn, args.schema)

    store_item_repo = StoreItemRepo(conn)
    obs_repo = ObservationRepo(conn)

    observed_on = args.date or dt.date.today().isoformat()

    items = store_item_repo.list_for_scrape()
    if not items:
        print("No tracked store_items. Use track-url first.")
        return

    inserted = skipped = failed = 0

    for si in items:
        try:
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

            tag = f"{si.family_key} {si.canonical_size:g}{si.canonical_unit}"
            if ok:
                inserted += 1
                print(f"[INSERT] {observed_on} {si.store_name} {tag}: {price_cents}c")
            else:
                skipped += 1
                print(f"[SKIP]   {observed_on} {si.store_name} {tag}: already observed")

        except Exception as e:
            failed += 1
            tag = f"{si.family_key} {si.canonical_size:g}{si.canonical_unit}"
            print(f"[FAIL]   {si.store_name} {tag} ({si.scraper}) {si.url} :: {e}")

    print(f"Done. inserted={inserted} skipped={skipped} failed={failed}")

def compute_normalized_unit_price(price_cents: int, size: float, unit: str):
    if size <= 0:
        return None
    eur = price_cents / 100.0
    u = unit.lower().strip()

    if u == "l":
        return eur / size, "l"
    if u == "ml":
        return eur / (size / 1000.0), "l"
    if u == "kg":
        return eur / size, "kg"
    if u == "g":
        return eur / (size / 1000.0), "kg"
    if u in {"pcs", "kos"}:
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
    points = obs_repo.history_by_family_key(args.key)

    if not points:
        print(f"No history for family key={args.key}")
        return

    # Trend per store+pack (zadnji 2 zapisa)
    if args.trend:
        rows = conn.execute(
            """
            SELECT
              s.name AS store_name,
              ci.label AS canonical_label,
              ci.size AS canonical_size,
              ci.unit AS canonical_unit,
              po.observed_on,
              po.price_cents
            FROM price_observation po
            JOIN store_item si ON si.id = po.store_item_id
            JOIN store s ON s.id = si.store_id
            JOIN canonical_item ci ON ci.id = si.canonical_item_id
            WHERE ci.family_key = ?
            ORDER BY s.name ASC, ci.unit ASC, ci.size ASC, po.observed_on DESC
            """,
            (args.key,),
        ).fetchall()

        latest2 = {}
        for r in rows:
            k = (str(r["store_name"]), str(r["canonical_label"]), float(r["canonical_size"]), str(r["canonical_unit"]))
            latest2.setdefault(k, [])
            if len(latest2[k]) < 2:
                latest2[k].append((str(r["observed_on"]), int(r["price_cents"])))

        print(f"Trend for {args.key} (zadnji 2 opazovanji per store+pack)")
        print("-" * 110)
        for (store, label, size, unit), pts in latest2.items():
            if len(pts) < 2:
                d1, p1 = pts[0]
                line = f"{store:<10} {size:g}{unit:<3} {label:<45}  n/a  last={p1/100:>7.2f} €  [{d1}]"
                if args.normalized:
                    norm = compute_normalized_unit_price(p1, size, unit)
                    if norm:
                        nv, nu = norm
                        line += f"  ({nv:.2f} €/{nu})"
                print(line)
                continue

            (d1, p1), (d2, p2) = pts[0], pts[1]
            arrow, delta, pct = _trend(p2, p1)
            line = (
                f"{store:<10} {size:g}{unit:<3} {label:<45}  "
                f"{arrow} {p1/100:>7.2f} €  {delta/100:+.2f} €  ({pct:+.1f}%)  [{d2}→{d1}]"
            )
            if args.normalized:
                norm = compute_normalized_unit_price(p1, size, unit)
                if norm:
                    nv, nu = norm
                    line += f"  ({nv:.2f} €/{nu})"
            print(line)
        print("-" * 110)

    print(f"History for {args.key} (rows={len(points)})")
    print("-" * 110)

    for p in points:
        eur = p.price_cents / 100.0
        line = (
            f"{p.observed_on}  {p.store_name:<10}  "
            f"{p.canonical_size:g}{p.canonical_unit:<3}  {eur:>7.2f} €  {p.canonical_label}"
        )
        if args.normalized:
            norm = compute_normalized_unit_price(p.price_cents, p.canonical_size, p.canonical_unit)
            if norm:
                nv, nu = norm
                line += f"  ({nv:.2f} €/{nu})"
        if args.show_title and p.title_raw:
            line += f"  |  {p.title_raw.strip()}"
        print(line)

def cmd_cheapest(args):
    conn = connect(args.db)
    init_db(conn, args.schema)

    rows = conn.execute(
        """
        SELECT
          s.name AS store_name,
          si.url AS url,
          ci.label AS canonical_label,
          ci.size AS canonical_size,
          ci.unit AS canonical_unit,
          po.observed_on AS observed_on,
          po.price_cents AS price_cents
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
        ORDER BY s.name ASC, ci.unit ASC, ci.size ASC
        """,
        (args.key,),
    ).fetchall()

    if not rows:
        print(f"No tracked store_items for family key={args.key} (use track-url).")
        return

    observed = []
    missing = []

    for r in rows:
        store = str(r["store_name"])
        size = float(r["canonical_size"])
        unit = str(r["canonical_unit"])
        label = str(r["canonical_label"])
        url = str(r["url"])

        if r["price_cents"] is None:
            missing.append(f"{store} {size:g}{unit}")
            continue

        price_cents = int(r["price_cents"])
        eur = price_cents / 100.0

        norm = compute_normalized_unit_price(price_cents, size, unit)
        if not norm:
            norm_val, norm_unit = eur, unit
        else:
            norm_val, norm_unit = norm

        observed.append({
            "store": store,
            "size": size,
            "unit": unit,
            "label": label,
            "eur": eur,
            "observed_on": str(r["observed_on"]),
            "url": url,
            "norm_val": float(norm_val),
            "norm_unit": norm_unit,
        })

    if not observed:
        print(f"No observations yet for family key={args.key}.")
        if missing:
            print("Missing:", ", ".join(missing))
        return

    observed.sort(key=lambda x: x["norm_val"])
    best = observed[0]

    print(f"Cheapest for {args.key} (by €/{best['norm_unit']})")
    print("-" * 110)
    print(
        f"{best['store']:<10}  {best['size']:g}{best['unit']:<3}  "
        f"{best['eur']:>7.2f} €  {best['label']:<45}  "
        f"({best['norm_val']:.2f} €/{best['norm_unit']})  [{best['observed_on']}]"
    )

    if args.show_url:
        print(f"url: {best['url']}")

    if args.show_all:
        print("-" * 110)
        print("Latest options:")
        for x in observed:
            print(
                f"{x['store']:<10}  {x['size']:g}{x['unit']:<3}  "
                f"{x['eur']:>7.2f} €  {x['label']:<45}  "
                f"({x['norm_val']:.2f} €/{x['norm_unit']})  [{x['observed_on']}]"
            )

    if missing:
        print("-" * 110)
        print("Missing (tracked but no observations yet):", ", ".join(sorted(set(missing))))

# ---------- argparse wiring ----------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="price-tracker-v2")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_db_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--db", default="data/prices.sqlite", help="Path to sqlite db file")
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
    p_hist.add_argument("--key", required=True, help="family_key, e.g. olive_oil, milk, eggs")
    p_hist.add_argument("--show-title", action="store_true", help="Include raw scraped title in output")
    p_hist.set_defaults(func=cmd_history)
    p_hist.add_argument("--normalized", action="store_true", help="Show normalized unit price (€/l, €/kg, €/pcs)",)
    p_hist.add_argument("--trend", action="store_true", help="Show trend vs previous observation (per store)")

    # cheapest
    p_ch = sub.add_parser("cheapest", help="Show cheapest store (latest price) for a canonical item key")
    add_db_args(p_ch)
    p_ch.add_argument("--key", required=True, help="family_key, e.g. olive_oil, milk, eggs")
    p_ch.add_argument("--normalized", action="store_true", help="Show normalized unit price (€/l, €/kg, €/pcs)")
    p_ch.add_argument("--show-all", action="store_true", help="Show latest price per store too")
    p_ch.add_argument("--show-url", action="store_true", help="Print URL for the cheapest entry")
    p_ch.add_argument("--show-title", action="store_true", help="Print raw scraped title for the cheapest entry")
    p_ch.set_defaults(func=cmd_cheapest)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()