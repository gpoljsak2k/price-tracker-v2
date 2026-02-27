from __future__ import annotations

import argparse
import datetime as dt
import time

from price_tracker.db import connect, init_db, transaction
from price_tracker.repos import StoreRepo, CanonicalItemRepo, StoreItemRepo, ObservationRepo

import os
import subprocess
import json
from collections import defaultdict
from typing import Any

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

    if failed > 0 and args.fail_on_error:
        raise SystemExit(2)

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

def cmd_sync(args):
    # 1) git pull
    if not os.path.isdir(".git"):
        print("Not a git repo (missing .git). Sync works only inside a git repository.")
        return

    pull_cmd = ["git", "pull", "--ff-only"]
    print("$ " + " ".join(pull_cmd))
    res = subprocess.run(pull_cmd, text=True, capture_output=True)

    if res.returncode != 0:
        # show useful error
        if res.stdout.strip():
            print(res.stdout.strip())
        if res.stderr.strip():
            print(res.stderr.strip())
        raise SystemExit(res.returncode)

    out = (res.stdout or "").strip()
    print(out if out else "Already up to date.")

    # 2) optional: show DB status (max observed_on)
    if args.show_db:
        conn = connect(args.db)
        init_db(conn, args.schema)

        row = conn.execute("SELECT MAX(observed_on) AS max_day FROM price_observation").fetchone()
        max_day = row["max_day"] if row and row["max_day"] is not None else None

        if max_day:
            print(f"DB latest observed_on: {max_day}")
        else:
            print("DB has no observations yet.")

def cmd_list_tracked(args):
    conn = connect(args.db)
    init_db(conn, args.schema)

    rows = conn.execute(
        """
        SELECT
          s.name AS store_name,
          ci.family_key AS family_key,
          ci.size AS size,
          ci.unit AS unit,
          ci.label AS label,
          si.scraper AS scraper,
          si.url AS url,
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
        ORDER BY s.name, ci.family_key, ci.unit, ci.size
        """
    ).fetchall()

    if not rows:
        print("No tracked store_items.")
        return

    print("Tracked items")
    print("-" * 120)

    for r in rows:
        store = r["store_name"]
        family = r["family_key"]
        size = float(r["size"])
        unit = r["unit"]
        label = r["label"]
        scraper = r["scraper"]
        url = r["url"]
        observed_on = r["observed_on"]
        price_cents = r["price_cents"]

        line = f"{store:<10} {family:<15} {size:g}{unit:<3}  {label:<35}  "

        if price_cents is None:
            line += "(no observations)"
        else:
            eur = int(price_cents) / 100.0
            line += f"{eur:>7.2f} €  [{observed_on}]"

            if args.normalized:
                norm = compute_normalized_unit_price(int(price_cents), size, unit)
                if norm:
                    nv, nu = norm
                    line += f"  ({nv:.2f} €/{nu})"

        print(line)

        if args.show_url:
            print(f"    scraper={scraper}  url={url}")

    print("-" * 120)

def cmd_doctor(args):
    conn = connect(args.db)
    init_db(conn, args.schema)

    repo = StoreItemRepo(conn)
    items = repo.list_for_scrape()

    if not items:
        print("No tracked store_items. Use track-url first.")
        return

    # optional filters
    if args.store:
        want = args.store.strip().lower()
        items = [x for x in items if x.store_name.lower() == want]

    if args.key:
        want = args.key.strip().lower()
        items = [x for x in items if x.family_key.lower() == want]

    if args.limit is not None:
        items = items[: int(args.limit)]

    if not items:
        print("No matching tracked store_items for given filters.")
        return

    ok = fail = 0
    print(f"Doctor check (items={len(items)})")
    print("-" * 120)

    for si in items:
        label = f"{si.family_key} {si.canonical_size:g}{si.canonical_unit} ({si.store_name})"
        t0 = time.perf_counter()
        try:
            price_cents, title_raw = scrape_url(
                si.scraper,
                si.url,
                verify_ssl=not args.insecure_ssl,
            )
            ms = int((time.perf_counter() - t0) * 1000)
            eur = int(price_cents) / 100.0

            norm_str = ""
            if args.normalized:
                norm = compute_normalized_unit_price(int(price_cents), si.canonical_size, si.canonical_unit)
                if norm:
                    nv, nu = norm
                    norm_str = f"  ({nv:.2f} €/{nu})"

            print(f"[OK]   {label:<45}  {eur:>7.2f} €{norm_str}  {ms}ms")
            if args.show_title and title_raw:
                print(f"       title={title_raw.strip()}")
            if args.show_url:
                print(f"       scraper={si.scraper} url={si.url}")
            ok += 1

        except Exception as e:
            ms = int((time.perf_counter() - t0) * 1000)
            print(f"[FAIL] {label:<45}  {ms}ms  :: {e}")
            if args.show_url:
                print(f"       scraper={si.scraper} url={si.url}")
            fail += 1

    print("-" * 120)
    print(f"Summary: ok={ok} failed={fail}")

    # make it CI-friendly if you want:
    if fail > 0 and args.fail_on_error:
        raise SystemExit(2)

def cmd_compare_list(args):
    conn = connect(args.db)
    init_db(conn, args.schema)

    # load list
    with open(args.list, "r", encoding="utf-8") as f:
        payload = json.load(f)

    items = payload.get("items", [])
    if not isinstance(items, list) or not items:
        print("Invalid shopping list: expected {\"items\": [...]} with at least 1 item")
        return

    want = []
    for it in items:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key", "")).strip()
        qty = it.get("qty", 1)
        try:
            qty = int(qty)
        except Exception:
            qty = 1
        if not key or qty <= 0:
            continue
        want.append((key, qty))

    if not want:
        print("Shopping list has no valid items.")
        return

    # get list of stores
    stores = conn.execute("SELECT id, name FROM store ORDER BY name").fetchall()
    if not stores:
        print("No stores in DB yet.")
        return

    # Prepare output accumulators per store
    per_store_total_cents = {int(s["id"]): 0 for s in stores}
    per_store_missing = {int(s["id"]): [] for s in stores}
    per_store_lines = {int(s["id"]): [] for s in stores}

    # For each requested family_key, pick best option per store
    for family_key, qty in want:
        # latest observed options per store for this family_key (across packs)
        rows = conn.execute(
            """
            SELECT
              s.id AS store_id,
              s.name AS store_name,
              ci.family_key AS family_key,
              ci.label AS label,
              ci.size AS size,
              ci.unit AS unit,
              si.url AS url,
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
            """,
            (family_key,),
        ).fetchall()

        # group by store_id and pick cheapest by normalized €/unit
        options_by_store = defaultdict(list)
        for r in rows:
            store_id = int(r["store_id"])
            if r["price_cents"] is None:
                continue
            price_cents = int(r["price_cents"])
            size = float(r["size"])
            unit = str(r["unit"])
            norm = compute_normalized_unit_price(price_cents, size, unit)
            if not norm:
                # if unsupported unit, fallback to pack price (still works, but less meaningful)
                norm_val, norm_unit = price_cents / 100.0, unit
            else:
                norm_val, norm_unit = norm

            options_by_store[store_id].append(
                {
                    "store_name": str(r["store_name"]),
                    "family_key": str(r["family_key"]),
                    "label": str(r["label"]),
                    "size": size,
                    "unit": unit,
                    "url": str(r["url"]),
                    "observed_on": str(r["observed_on"]),
                    "price_cents": price_cents,
                    "norm_val": float(norm_val),
                    "norm_unit": norm_unit,
                }
            )

        for s in stores:
            sid = int(s["id"])
            sname = str(s["name"])

            opts = options_by_store.get(sid, [])
            if not opts:
                per_store_missing[sid].append(f"{family_key} x{qty}")
                continue

            best = min(opts, key=lambda x: x["norm_val"])
            line_total = best["price_cents"] * qty
            per_store_total_cents[sid] += line_total

            price_eur = best["price_cents"] / 100.0
            line_total_eur = line_total / 100.0
            per_unit = f"{best['norm_val']:.2f} €/{best['norm_unit']}"

            per_store_lines[sid].append(
                f"- {family_key:<12} x{qty:<2}  "
                f"{best['size']:g}{best['unit']:<3} {price_eur:>7.2f} €  "
                f"({per_unit})  -> {line_total_eur:>7.2f} €   [{best['observed_on']}]  {best['label']}"
                + (f"\n    {best['url']}" if args.show_url else "")
            )

    # Print results
    print(f"Compare list: {args.list}")
    print("=" * 120)

    # sort stores by total (missing-heavy stores last)
    def sort_key(sid: int):
        missing_count = len(per_store_missing[sid])
        return (missing_count, per_store_total_cents[sid])

    for s in sorted(stores, key=lambda r: sort_key(int(r["id"]))):
        sid = int(s["id"])
        sname = str(s["name"])
        total_eur = per_store_total_cents[sid] / 100.0
        missing = per_store_missing[sid]

        print(f"{sname}  total={total_eur:.2f} €   missing={len(missing)}")
        print("-" * 120)

        lines = per_store_lines[sid]
        if lines:
            for ln in lines:
                print(ln)
        else:
            print("(no matched items)")

        if missing:
            print("\nMissing:")
            for m in missing:
                print(f"  - {m}")
        print("=" * 120)

def _load_shopping_list(path: str) -> dict[str, Any]:
    path = path.strip()
    if not path:
        raise ValueError("list path is empty")

    if not os.path.exists(path):
        return {"items": []}

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("shopping list must be a JSON object")

    items = data.get("items")
    if items is None:
        data["items"] = []
    elif not isinstance(items, list):
        raise ValueError('shopping list must contain "items": [...]')

    # normalize entries
    norm_items = []
    for it in data["items"]:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key", "")).strip()
        if not key:
            continue
        qty = it.get("qty", 1)
        try:
            qty = int(qty)
        except Exception:
            qty = 1
        if qty <= 0:
            continue
        norm_items.append({"key": key, "qty": qty})

    data["items"] = norm_items
    return data

def _save_shopping_list(path: str, data: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

def cmd_list_show(args):
    data = _load_shopping_list(args.list)
    items = data.get("items", [])
    print(f"Shopping list: {args.list}")
    print("-" * 60)
    if not items:
        print("(empty)")
        return
    for it in items:
        print(f"- {it['key']:<20} qty={it['qty']}")

def cmd_list_add(args):
    key = args.key.strip()
    if not key:
        raise SystemExit("Error: --key cannot be empty")

    qty = int(args.qty)
    if qty <= 0:
        raise SystemExit("Error: --qty must be > 0")

    data = _load_shopping_list(args.list)
    items = data["items"]

    for it in items:
        if it["key"] == key:
            it["qty"] = qty
            _save_shopping_list(args.list, data)
            print(f"OK: updated {key} qty={qty} in {args.list}")
            return

    items.append({"key": key, "qty": qty})
    items.sort(key=lambda x: x["key"])
    _save_shopping_list(args.list, data)
    print(f"OK: added {key} qty={qty} to {args.list}")

def cmd_list_rm(args):
    key = args.key.strip()
    if not key:
        raise SystemExit("Error: --key cannot be empty")

    data = _load_shopping_list(args.list)
    items = data["items"]
    before = len(items)
    items = [it for it in items if it["key"] != key]
    data["items"] = items

    if len(items) == before:
        print(f"Nothing to remove: {key} not found in {args.list}")
        return

    _save_shopping_list(args.list, data)
    print(f"OK: removed {key} from {args.list}")

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
    p_scrape.add_argument("--fail-on-error", action="store_true", help="Exit with non-zero code if any scrape fails (CI-friendly)",)

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

    # sync
    p_sync = sub.add_parser("sync", help="Git pull (fast-forward) and optionally show DB latest date")
    add_db_args(p_sync)
    p_sync.add_argument("--show-db", action="store_true", help="Show DB latest observed_on after pull")
    p_sync.set_defaults(func=cmd_sync)

    # list-tracked
    p_list = sub.add_parser("list-tracked", help="List all tracked store items")
    add_db_args(p_list)
    p_list.add_argument("--show-url", action="store_true", help="Show URL and scraper")
    p_list.add_argument("--normalized", action="store_true", help="Show normalized €/unit")
    p_list.set_defaults(func=cmd_list_tracked)

    # healthcheck for scrapers
    p_doc = sub.add_parser("doctor", help="Healthcheck: try scraping tracked URLs without writing to DB")
    add_db_args(p_doc)
    p_doc.add_argument("--store", default=None, help="Filter by store name (e.g. Mercator)")
    p_doc.add_argument("--key", default=None, help="Filter by family key (e.g. olive_oil)")
    p_doc.add_argument("--limit", type=int, default=None, help="Limit number of checks")
    p_doc.add_argument("--insecure-ssl", action="store_true", help="DEV ONLY: disable SSL verification")
    p_doc.add_argument("--show-url", action="store_true", help="Print URL + scraper")
    p_doc.add_argument("--show-title", action="store_true", help="Print raw scraped title")
    p_doc.add_argument("--normalized", action="store_true", help="Show normalized €/unit")
    p_doc.add_argument("--fail-on-error", action="store_true", help="Exit with non-zero code if any FAIL")
    p_doc.set_defaults(func=cmd_doctor)

    # compare-list
    p_cmp = sub.add_parser("compare-list", help="Compare a shopping list across stores")
    add_db_args(p_cmp)
    p_cmp.add_argument("--list", required=True, help="Path to shopping_list.json")
    p_cmp.add_argument("--show-url", action="store_true", help="Show selected item URL per store")
    p_cmp.set_defaults(func=cmd_compare_list)

    # list-show
    p_ls = sub.add_parser("list-show", help="Show shopping list JSON")
    p_ls.add_argument("--list", required=True, help="Path to shopping_list.json")
    p_ls.set_defaults(func=cmd_list_show)

    # list-add
    p_la = sub.add_parser("list-add", help="Add/update an item in shopping list JSON")
    p_la.add_argument("--list", required=True, help="Path to shopping_list.json")
    p_la.add_argument("--key", required=True, help="Family key, e.g. milk, eggs, olive_oil")
    p_la.add_argument("--qty", type=int, default=1, help="Quantity (number of packs)")
    p_la.set_defaults(func=cmd_list_add)

    # list-rm
    p_lr = sub.add_parser("list-rm", help="Remove an item from shopping list JSON")
    p_lr.add_argument("--list", required=True, help="Path to shopping_list.json")
    p_lr.add_argument("--key", required=True, help="Family key to remove")
    p_lr.set_defaults(func=cmd_list_rm)

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()