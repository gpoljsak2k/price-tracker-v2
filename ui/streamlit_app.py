from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json
import sqlite3

import pandas as pd
import streamlit as st

from price_tracker.services.analytics_service import AnalyticsService
from price_tracker.utils import compute_normalized_unit_price

DEFAULT_DB = "data/prices.sqlite"

def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def history_df(conn: sqlite3.Connection, family_key: str) -> pd.DataFrame:
    """
    Temporary: keeps SQL here for now.
    Next step: move into AnalyticsService.history().
    """
    rows = conn.execute(
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

    data = []
    for r in rows:
        price_cents = int(r["price_cents"])
        size = float(r["size"])
        unit = str(r["unit"])

        eur = price_cents / 100.0
        norm = compute_normalized_unit_price(price_cents, size, unit)
        eur_per_unit = float(norm[0]) if norm else None
        norm_unit = norm[1] if norm else None

        data.append(
            {
                "date": r["observed_on"],
                "store": r["store"],
                "pack": f"{size:g}{unit}",
                "price_eur": eur,
                "eur_per_unit": eur_per_unit,
                "unit": norm_unit,
                "label": r["label"],
            }
        )

    return pd.DataFrame(data)


def compare_list(conn: sqlite3.Connection, shopping: dict) -> dict:
    """
    Minimal version for UI.
    Next step: move into service layer (shared with CLI compare-list).
    """
    items = shopping.get("items", [])
    want = []
    for it in items:
        key = str(it.get("key", "")).strip()
        qty = it.get("qty", 1)
        try:
            qty = int(qty)
        except Exception:
            qty = 1
        if key and qty > 0:
            want.append((key, qty))

    stores = conn.execute("SELECT id, name FROM store ORDER BY name").fetchall()
    results = {}

    for s in stores:
        sid = int(s["id"])
        sname = str(s["name"])
        total_cents = 0
        missing = []
        chosen = []

        for family_key, qty in want:
            rows = conn.execute(
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

            opts = []
            for r in rows:
                if r["price_cents"] is None:
                    continue
                pc = int(r["price_cents"])
                size = float(r["size"])
                unit = str(r["unit"])
                norm = compute_normalized_unit_price(pc, size, unit)
                nv = float(norm[0]) if norm else (pc / 100.0)
                nu = norm[1] if norm else unit
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
                    "price (€)": pc / 100.0,
                    "€/unit": nv,
                    "unit": nu,
                    "date": best["observed_on"],
                    "label": best["label"],
                    "url": best["url"],
                }
            )

        results[sname] = {
            "total (€)": total_cents / 100.0,
            "missing": missing,
            "chosen": chosen,
        }

    return results


# ------------------ UI ------------------

st.set_page_config(page_title="Price Tracker V2", layout="wide")
st.title("Price Tracker V2")

with st.sidebar:
    st.header("Settings")
    db_path = st.text_input("DB path", value=DEFAULT_DB)
    st.caption("Tip: GitHub Actions updates this DB. Use `python app.py sync --discard-db` locally.")

    if not Path(db_path).exists():
        st.error(f"DB not found: {db_path}")
        st.stop()

    conn = connect(db_path)
    svc = AnalyticsService(conn)

    families = svc.list_families()
    if not families:
        st.warning("No families found. Add items via CLI track-url first.")
        st.stop()

    family = st.selectbox("Family key", families, index=0)

tab1, tab2, tab3 = st.tabs(["Latest", "History", "Shopping list"])

with tab1:
    st.subheader(f"Latest prices: {family}")

    latest = svc.latest_prices(family)
    df = pd.DataFrame(latest)

    if df.empty:
        st.info("No tracked items for this family.")
    else:
        df_ui = df.rename(
            columns={
                "price_eur": "price (€)",
                "eur_per_unit": "€/unit",
                "date": "date",
            }
        )
        st.dataframe(df_ui[["store", "pack", "price (€)", "€/unit", "unit", "date", "label"]], use_container_width=True)

        cand = df.dropna(subset=["eur_per_unit"])
        if not cand.empty:
            best = cand.sort_values("eur_per_unit").iloc[0]
            st.success(
                f"Cheapest today: {best['store']} {best['pack']} — {best['price_eur']:.2f} € "
                f"({best['eur_per_unit']:.2f} €/{best['unit']})"
            )
        else:
            st.info("No normalized €/unit data available for this family (units unsupported).")

with tab2:
    st.subheader(f"History: {family}")

    hdf = history_df(conn, family)
    if hdf.empty:
        st.info("No history yet.")
    else:
        plot = hdf.dropna(subset=["eur_per_unit"]).copy()
        if plot.empty:
            st.warning("No normalized unit history available. Showing pack price instead.")
            plot = hdf.copy()
            plot["value"] = plot["price_eur"]
            st.line_chart(plot, x="date", y="value", color="store")
        else:
            plot["value"] = plot["eur_per_unit"]
            st.line_chart(plot, x="date", y="value", color="store")

        with st.expander("Raw history table"):
            st.dataframe(hdf, use_container_width=True)

with tab3:
    st.subheader("Shopping list compare")
    st.caption('Upload a JSON list: { "items": [ {"key":"milk","qty":2}, ... ] }')

    uploaded = st.file_uploader("Upload shopping_list.json", type=["json"])
    example = {"items": [{"key": "olive_oil", "qty": 1}]}
    st.code(json.dumps(example, ensure_ascii=False, indent=2), language="json")

    if uploaded is not None:
        try:
            shopping = json.load(uploaded)
            res = compare_list(conn, shopping)

            items_sorted = sorted(
                res.items(),
                key=lambda kv: (len(kv[1]["missing"]), kv[1]["total (€)"]),
            )

            for store, info in items_sorted:
                st.markdown(
                    f"### {store} — total **{info['total (€)']:.2f} €**  (missing: {len(info['missing'])})"
                )

                if info["chosen"]:
                    st.dataframe(pd.DataFrame(info["chosen"]), use_container_width=True)

                if info["missing"]:
                    st.warning("Missing: " + ", ".join(info["missing"]))

        except Exception as e:
            st.error(f"Invalid JSON: {e}")