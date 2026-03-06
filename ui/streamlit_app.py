from __future__ import annotations

import sys
import math
import json
import sqlite3
from pathlib import Path
from contextlib import contextmanager
import subprocess

import pandas as pd
import streamlit as st
import altair as alt

# --- project root import hack ---
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from price_tracker.services.analytics_service import AnalyticsService  # noqa: E402


DEFAULT_DB = "data/prices.sqlite"


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection, schema_path: str) -> None:
    sql = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()

def reset_db(db_path: str, schema_path: str) -> None:
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    # delete old db if exists
    if p.exists():
        p.unlink()

    # recreate + init schema
    conn = connect(str(p))
    init_db(conn, schema_path)
    conn.close()

# ----------------- SQL helpers for Add items (track-url UI) -----------------

@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        conn.execute("BEGIN")
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def store_get_or_create(conn: sqlite3.Connection, store_name: str) -> int:
    store_name = store_name.strip()
    if not store_name:
        raise ValueError("Store is required.")

    row = conn.execute("SELECT id FROM store WHERE name = ?", (store_name,)).fetchone()
    if row:
        return int(row["id"])

    conn.execute("INSERT INTO store(name) VALUES (?)", (store_name,))
    row = conn.execute("SELECT id FROM store WHERE name = ?", (store_name,)).fetchone()
    return int(row["id"])


def canonical_upsert(conn: sqlite3.Connection, family_key: str, label: str, size: float, unit: str) -> int:
    """
    Mirrors CLI CanonicalItemRepo.upsert():
    UNIQUE(family_key, size, unit). If exists -> UPDATE label. Else INSERT.
    """
    family_key = family_key.strip()
    label = label.strip()
    unit = unit.strip()

    if not family_key:
        raise ValueError("Key (--key / family_key) is required.")
    if not label:
        raise ValueError("Food name (--food name) is required.")
    if size <= 0:
        raise ValueError("Size (--size) must be > 0.")
    if not unit:
        raise ValueError("Unit (--unit) is required.")

    row = conn.execute(
        """
        SELECT id FROM canonical_item
        WHERE family_key = ? AND size = ? AND unit = ?
        """,
        (family_key, float(size), unit),
    ).fetchone()

    if row:
        canonical_id = int(row["id"])
        conn.execute("UPDATE canonical_item SET label = ? WHERE id = ?", (label, canonical_id))
        return canonical_id

    conn.execute(
        """
        INSERT INTO canonical_item(family_key, label, size, unit)
        VALUES (?, ?, ?, ?)
        """,
        (family_key, label, float(size), unit),
    )

    row = conn.execute(
        """
        SELECT id FROM canonical_item
        WHERE family_key = ? AND size = ? AND unit = ?
        """,
        (family_key, float(size), unit),
    ).fetchone()
    return int(row["id"])


def store_item_upsert_mapping(
    conn: sqlite3.Connection,
    store_id: int,
    canonical_item_id: int,
    url: str,
    scraper: str,
    label_override: str | None,
) -> int:
    """
    Mirrors CLI StoreItemRepo.upsert_mapping():
    - If mapping (store_id, canonical_item_id) exists -> UPDATE url/scraper/label_override
    - Else INSERT
    - UNIQUE(url) enforced
    """
    url = url.strip()
    scraper = scraper.strip()
    if not url:
        raise ValueError("URL (--url) is required.")
    if not scraper:
        raise ValueError("Scraper (--scraper) is required.")

    row = conn.execute(
        """
        SELECT id FROM store_item
        WHERE store_id = ? AND canonical_item_id = ?
        """,
        (int(store_id), int(canonical_item_id)),
    ).fetchone()

    if row:
        store_item_id = int(row["id"])
        try:
            conn.execute(
                """
                UPDATE store_item
                SET url = ?, scraper = ?, label_override = ?
                WHERE id = ?
                """,
                (url, scraper, (label_override or None), store_item_id),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError("This URL is already tracked for a different item (UNIQUE url).") from e
        return store_item_id

    try:
        conn.execute(
            """
            INSERT INTO store_item(store_id, canonical_item_id, url, scraper, label_override)
            VALUES (?, ?, ?, ?, ?)
            """,
            (int(store_id), int(canonical_item_id), url, scraper, (label_override or None)),
        )
    except sqlite3.IntegrityError as e:
        msg = str(e).lower()
        if "unique constraint failed: store_item.url" in msg:
            raise ValueError("This URL is already tracked for a different item (UNIQUE url).") from e
        raise

    row = conn.execute("SELECT id FROM store_item WHERE url = ?", (url,)).fetchone()
    return int(row["id"])


def delete_store_item(conn: sqlite3.Connection, store_item_id: int) -> None:
    conn.execute("DELETE FROM store_item WHERE id = ?", (int(store_item_id),))
    conn.commit()

def delete_canonical_item(conn: sqlite3.Connection, canonical_item_id: int) -> None:
    conn.execute("DELETE FROM canonical_item WHERE id = ?", (int(canonical_item_id),))
    conn.commit()


# ----------------- UI helpers -----------------

def fmt2(x) -> str:
    """Format float to 2 decimals; return '—' if None/NaN/unparseable."""
    try:
        if x is None:
            return "—"
        x = float(x)
        if math.isnan(x):
            return "—"
        return f"{x:.2f}"
    except Exception:
        return "—"


def list_family_labels(conn: sqlite3.Connection) -> dict[str, str]:
    """
    Returns mapping: family_key -> representative canonical label (food name)
    """
    rows = conn.execute(
        """
        SELECT family_key, MIN(label) AS food_name
        FROM canonical_item
        GROUP BY family_key
        ORDER BY family_key
        """
    ).fetchall()
    return {str(r["family_key"]): str(r["food_name"]) for r in rows}


def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


# ----------------- UI -----------------

st.set_page_config(page_title="Price Tracker V2", layout="wide")
st.title("Price Tracker V2")
st.caption("Streamlit frontend • pricer-tracker-v2")

# ----- sidebar (DB init + service init) -----
with st.sidebar:
    st.header("Settings")
    db_path = st.text_input("DB path", value=DEFAULT_DB)

    c1, c2 = st.columns(2)
    with c1:
        refresh = st.button("Refresh", use_container_width=True)
    with c2:
        show_dev = st.toggle("Dev", value=False)

    st.caption("Tip: GitHub Actions updates this DB. Use `python app.py sync --discard-db` locally.")

    schema_path = str(ROOT / "schema.sql")

    # If DB missing, allow creating it so Tab4 is usable
    if not Path(db_path).exists():
        st.warning(f"DB not found: {db_path}")
        if st.button("Create empty DB (init schema)", use_container_width=True):
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            conn = connect(db_path)
            init_db(conn, schema_path)
            st.success("DB created + schema initialized.")
            st.rerun()

    st.divider()
    with st.expander("Help (quick)"):
        st.markdown("""
    - **Start**: Create DB if missing → go to **Add items** → Track URL → **Scrape all**
    - **Latest**: shows latest prices per store + cheapest
    - **History**: filter stores + date range + see trend table + chart
    - **Shopping list**: build basket, compare (single/split), export CSV
    """)


    st.subheader("RESET DATABASE (USE CAREFULLY!!!)")

    with st.expander("Reset database"):
        st.warning("This will DELETE the SQLite database file and create a new empty DB.")
        st.caption("Use this if you want a clean start (all products, URLs and observations will be lost).")

        confirm_text = st.text_input('Type "RESET" to confirm', value="", key="reset_confirm_text")
        confirm_check = st.checkbox("I understand this is irreversible", value=False, key="reset_confirm_check")

        do_reset = st.button(
            "Reset DB now",
            type="primary",
            use_container_width=True,
            disabled=not (confirm_check and confirm_text.strip().upper() == "RESET"),
            key="reset_db_btn",
        )

        if do_reset:
            try:
                # close current connection if it exists in this scope
                try:
                    conn.close()
                except Exception:
                    pass

                reset_db(db_path, schema_path)

                # clear UI state so selectors don't keep invalid values
                st.cache_data.clear()
                st.session_state.clear()

                st.success("DB reset ✅ New empty database created.")
                st.rerun()
            except Exception as e:
                st.error(f"Reset failed: {e}")

    # Always connect (SQLite will create file if missing)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    init_db(conn, schema_path)

    svc = AnalyticsService(conn)

    st.divider()
    st.subheader("DB status")

    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(DISTINCT family_key) FROM canonical_item) AS families,
          (SELECT COUNT(*) FROM canonical_item) AS canonical_items,
          (SELECT COUNT(*) FROM store_item) AS tracked_urls,
          (SELECT COUNT(*) FROM price_observation) AS observations,
          (SELECT COUNT(*) FROM store) AS stores,
          (SELECT MAX(observed_on) FROM price_observation) AS latest_day
        """
    ).fetchone()

    r1c1, r1c2 = st.columns(2)
    r1c1.metric("Keys", int(row["families"] or 0))
    r1c2.metric("Stores", int(row["stores"] or 0))

    r2c1, r2c2 = st.columns(2)
    r2c1.metric("Food name and unit", int(row["canonical_items"] or 0))
    r2c2.metric("Tracked URLs", int(row["tracked_urls"] or 0))

    r3c1, r3c2 = st.columns(2)
    r3c1.metric("Observations", int(row["observations"] or 0))
    r3c2.metric("Latest day", row["latest_day"] or "—")

    families = svc.list_families()  # may be []
    if not families:
        st.warning("No families found yet. Go to **Add items** tab to add the first product.")

if refresh:
    st.cache_data.clear()

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Latest", "History", "Shopping list", "Add items", "Help"])


# ===================== TAB 1: LATEST =====================
def render_tab1():
    if not families:
        st.info("No products yet. Go to **Add items** to add the first one.")
        return

    if "selected_family_latest" not in st.session_state:
        st.session_state.selected_family_latest = families[0]
    if st.session_state.selected_family_latest not in families:
        st.session_state.selected_family_latest = families[0]

    family = st.selectbox("Select product (--key)", families, key="selected_family_latest")
    st.subheader(f"Latest prices: {family}")

    latest = svc.latest_prices(family)
    df = pd.DataFrame(latest)

    if df.empty:
        st.info("No tracked items for this family.")
        return

    df_ui = df.rename(
        columns={
            "price_eur": "price (€)",
            "eur_per_unit": "€/unit",
            "food_name": "food name",
            "label": "brand",
        }
    ).copy()

    if "price (€)" in df_ui.columns:
        df_ui["price (€)"] = pd.to_numeric(df_ui["price (€)"], errors="coerce")
    if "€/unit" in df_ui.columns:
        df_ui["€/unit"] = pd.to_numeric(df_ui["€/unit"], errors="coerce")

    has_unit = ("€/unit" in df_ui.columns) and (not df_ui["€/unit"].dropna().empty)
    best_row = None

    if has_unit:
        cand = df_ui.dropna(subset=["€/unit"])
        if not cand.empty:
            best_row = cand.sort_values(["€/unit", "price (€)"], na_position="last").iloc[0]
            best_msg = (
                f"Najcenejši (€/unit): **{best_row.get('brand','')}** — "
                f"**{best_row.get('store','')}** • {best_row.get('pack','')} • "
                f"**{fmt2(best_row.get('price (€)'))} €** "
                f"({fmt2(best_row.get('€/unit'))} €/{best_row.get('unit','')})"
            )
        else:
            best_msg = "Najcenejši (€/unit): —"
    else:
        cand = df_ui.dropna(subset=["price (€)"]) if "price (€)" in df_ui.columns else df_ui.iloc[0:0]
        if not cand.empty:
            best_row = cand.sort_values(["price (€)"], na_position="last").iloc[0]
            best_msg = (
                f"Najcenejši: **{best_row.get('brand','')}** — "
                f"**{best_row.get('store','')}** • {best_row.get('pack','')} • "
                f"**{fmt2(best_row.get('price (€)'))} €**"
            )
        else:
            best_msg = "Najcenejši: — (no price data available)"

    st.success(best_msg)

    df_ui["best"] = ""
    if best_row is not None:
        df_ui.loc[df_ui.index == best_row.name, "best"] = "✅"

    cols = [c for c in ["best", "store", "food name", "brand", "pack", "price (€)", "€/unit", "unit", "date"] if c in df_ui.columns]
    if "best" in df_ui.columns:
        df_ui = df_ui.sort_values(["best", "store"], ascending=[False, True])

    st.dataframe(df_ui[cols], use_container_width=True, hide_index=True)

    if show_dev:
        with st.expander("Dev: raw latest"):
            st.write(df)


with tab1:
    render_tab1()


# ===================== TAB 2: HISTORY =====================
def render_tab2():
    if not families:
        st.info("No products yet. Go to **Add items** to add the first one.")
        return

    if "selected_family_history" not in st.session_state:
        st.session_state.selected_family_history = families[0]
    if st.session_state.selected_family_history not in families:
        st.session_state.selected_family_history = families[0]

    family = st.selectbox("Select product (--key)", families, key="selected_family_history")
    st.subheader(f"History: {family}")

    hdf = pd.DataFrame(svc.history(family))
    if hdf.empty:
        st.info("No history yet.")
        return

    hdf = hdf.copy()

    if "date" not in hdf.columns:
        st.error("History data missing 'date' column.")
        return

    hdf["date"] = pd.to_datetime(hdf["date"], errors="coerce")
    for col in ["store", "label", "pack", "unit"]:
        if col in hdf.columns:
            hdf[col] = hdf[col].astype(str)

    if "price_eur" in hdf.columns:
        hdf["price_eur"] = pd.to_numeric(hdf["price_eur"], errors="coerce")
    if "eur_per_unit" in hdf.columns:
        hdf["eur_per_unit"] = pd.to_numeric(hdf["eur_per_unit"], errors="coerce")

    # choose product/brand inside family
    if "label" in hdf.columns and hdf["label"].notna().any():
        labels = sorted(hdf["label"].dropna().unique().tolist())
        chosen_label = st.selectbox("Product (--brand)", labels, index=0)
        base = hdf[hdf["label"] == chosen_label].copy()
    else:
        st.warning("History nima stolpca 'label' → izbira artikla ni možna.")
        base = hdf.copy()

    # optional pack filter
    if "pack" in base.columns and base["pack"].notna().any():
        packs = sorted(base["pack"].dropna().unique().tolist())
        if len(packs) > 1:
            chosen_pack = st.selectbox("Pakiranje (pack)", ["(all)"] + packs, index=0)
            if chosen_pack != "(all)":
                base = base[base["pack"] == chosen_pack].copy()

    stores = sorted(base["store"].dropna().unique().tolist()) if "store" in base.columns else []
    r1c1, r1c2, r1c3, r1c4 = st.columns([2, 3, 3, 2])
    with r1c1:
        metric = st.selectbox("Metric", ["€/unit", "price (€)"], index=0)
    with r1c2:
        store_sel = st.multiselect("Stores", options=stores, default=stores)
    with r1c3:
        smoothing = st.selectbox("Smoothing (graph only)", ["none", "3-point", "7-point"], index=0)
    with r1c4:
        show_raw = st.toggle("Show raw table", value=False)

    plot = base.copy()
    if store_sel and "store" in plot.columns:
        plot = plot[plot["store"].isin(store_sel)].copy()

    plot = plot.dropna(subset=["date"])
    if plot.empty:
        st.info("No dated points for this selection.")
        return

    dmin = plot["date"].min().date()
    dmax = plot["date"].max().date()

    if dmin == dmax:
        st.info(f"Only one date available: {dmin} (date range slider disabled)")
        dr = (dmin, dmax)
    else:
        dr = st.slider("Date range", min_value=dmin, max_value=dmax, value=(dmin, dmax))

    plot = plot[(plot["date"].dt.date >= dr[0]) & (plot["date"].dt.date <= dr[1])].copy()
    if plot.empty:
        st.info("No points in selected date range.")
        return

    # y value
    if metric == "€/unit":
        if "eur_per_unit" in plot.columns and plot["eur_per_unit"].notna().any():
            y_label = "€/unit"
            plot["value"] = plot["eur_per_unit"]
        else:
            st.info("Ni €/unit za ta artikel → fallback na pack price.")
            y_label = "price (€)"
            plot["value"] = plot["price_eur"]
    else:
        y_label = "price (€)"
        plot["value"] = plot["price_eur"]

    plot = plot.dropna(subset=["value", "price_eur"])
    if plot.empty:
        st.info("No numeric values to plot after filters.")
        return

    trend_src = plot.copy()

    def build_cli_trend_table(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame()

        d = df.copy()
        d["date"] = pd.to_datetime(d["date"], errors="coerce")
        d = d.dropna(subset=["date"])

        for col in ["store", "pack", "label"]:
            if col not in d.columns:
                d[col] = ""
            d[col] = d[col].astype(str)

        d["price_eur"] = pd.to_numeric(d.get("price_eur"), errors="coerce")
        if "eur_per_unit" in d.columns:
            d["eur_per_unit"] = pd.to_numeric(d.get("eur_per_unit"), errors="coerce")

        rows = []
        rng_min = d["date"].min()
        rng_max = d["date"].max()
        rng_str = f"[{rng_min.date()}→{rng_max.date()}]"

        for (store, pack), g in d.sort_values("date").groupby(["store", "pack"], dropna=False):
            g = g.dropna(subset=["price_eur"])
            if g.empty:
                continue

            first = g.iloc[0]
            last = g.iloc[-1]
            prev = g.iloc[-2] if len(g) >= 2 else None

            first_price = float(first["price_eur"])
            last_price = float(last["price_eur"])
            prev_price = float(prev["price_eur"]) if prev is not None and pd.notna(prev["price_eur"]) else None

            delta = last_price - first_price
            delta_pct = (delta / first_price * 100.0) if first_price != 0 else None

            if prev_price is None:
                arrow = "•"
            elif last_price > prev_price:
                arrow = "↑"
            elif last_price < prev_price:
                arrow = "↓"
            else:
                arrow = "→"

            unit_val = None
            if "eur_per_unit" in g.columns and pd.notna(last.get("eur_per_unit")):
                unit_val = float(last["eur_per_unit"])

            rows.append(
                {
                    "store": store,
                    "pack": pack,
                    "brand": last.get("label", ""),
                    "tr": arrow,
                    "price": last_price,
                    "Δ": delta,
                    "%": delta_pct,
                    "range": rng_str,
                    "€/unit": unit_val,
                }
            )

        tdf = pd.DataFrame(rows)
        if tdf.empty:
            return tdf

        if tdf["€/unit"].notna().any():
            tdf = tdf.sort_values(["€/unit", "price"], na_position="last")
        else:
            tdf = tdf.sort_values(["price"], na_position="last")

        tdf.insert(0, "best", ["✅" if i == 0 else "" for i in range(len(tdf))])
        return tdf

    trend_df = build_cli_trend_table(trend_src)

    st.markdown("### Trend (table)")
    if trend_df.empty:
        st.info("No trend rows available for this selection.")
    else:
        st.dataframe(
            trend_df,
            use_container_width=True,
            hide_index=True,
            height=140,
            column_config={
                "store": st.column_config.TextColumn("store", width="medium"),
                "pack": st.column_config.TextColumn("pack", width="small"),
                "label": st.column_config.TextColumn("brand", width="medium"),
                "tr": st.column_config.TextColumn("tr", width="small"),
                "price": st.column_config.NumberColumn("price", format="%.2f €"),
                "Δ": st.column_config.NumberColumn("Δ", format="%+.2f €"),
                "%": st.column_config.NumberColumn("%", format="%+.1f"),
                "range": st.column_config.TextColumn("range", width="medium"),
                "€/unit": st.column_config.NumberColumn("€/unit", format="%.2f"),
            },
        )

    # smoothing for graph only
    plot_graph = plot.copy()
    if smoothing != "none" and "store" in plot_graph.columns:
        window = 3 if smoothing == "3-point" else 7
        plot_graph = plot_graph.sort_values(["store", "date"])
        plot_graph["value"] = plot_graph.groupby("store")["value"].transform(
            lambda s: s.rolling(window, min_periods=1).mean()
        )

    st.markdown(f"### Trend ({y_label})")
    tooltip = [
        alt.Tooltip("store:N", title="store"),
        alt.Tooltip("date:T", title="date"),
        alt.Tooltip("value:Q", title=y_label, format=".2f"),
        alt.Tooltip("price_eur:Q", title="price (€)", format=".2f"),
    ]
    if "pack" in plot_graph.columns:
        tooltip.append(alt.Tooltip("pack:N", title="pack"))
    if "label" in plot_graph.columns:
        tooltip.append(alt.Tooltip("label:N", title="label"))

    chart = (
        alt.Chart(plot_graph.dropna(subset=["date", "value"]))
        .mark_line(point=True)
        .encode(
            x=alt.X("date:T", title="date"),
            y=alt.Y("value:Q", title=y_label),
            color=alt.Color("store:N", title="store"),
            tooltip=tooltip,
        )
        .properties(height=380)
    )
    st.altair_chart(chart, use_container_width=True)

    if show_raw:
        with st.expander("Raw filtered table"):
            show_cols = [c for c in ["date", "store", "label", "pack", "price_eur", "eur_per_unit", "unit"] if c in plot.columns]
            st.dataframe(plot.sort_values(["date", "store"])[show_cols], use_container_width=True, hide_index=True)

    if show_dev:
        with st.expander("Dev: raw history"):
            st.write(hdf)


with tab2:
    render_tab2()


# ===================== TAB 3: SHOPPING LIST =====================
def render_tab3():
    if not families:
        st.info("No products yet. Go to **Add items** to add the first one.")
        return

    st.subheader("Shopping list")
    st.caption("Build a list, compare prices across stores, and export JSON/CSV.")

    # ---- session state
    if "shopping_items" not in st.session_state:
        st.session_state.shopping_items = []

    def normalize_items(items: list[dict]) -> list[dict]:
        qty_by_key: dict[str, int] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            k = str(it.get("key", "")).strip()
            try:
                q = int(it.get("qty", 1))
            except Exception:
                q = 1
            if not k or q <= 0:
                continue
            qty_by_key[k] = qty_by_key.get(k, 0) + q
        out = [{"key": k, "qty": q} for k, q in qty_by_key.items()]
        out.sort(key=lambda x: x["key"])
        return out

    st.session_state.shopping_items = normalize_items(st.session_state.shopping_items)

    family_labels = list_family_labels(conn)
    family_options = []
    for k in families:
        name = family_labels.get(k, "")
        family_options.append(f"{k} — {name}" if name else k)

    def parse_key(option: str) -> str:
        return option.split(" — ", 1)[0].strip()

    st.markdown("### Builder")
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        opt = st.selectbox("Item", family_options, index=0)
        add_key = parse_key(opt)
    with c2:
        add_qty = st.number_input("Qty", min_value=1, value=1, step=1)
    with c3:
        add_btn = st.button("Add", use_container_width=True)

    if add_btn:
        st.session_state.shopping_items.append({"key": add_key, "qty": int(add_qty)})
        st.session_state.shopping_items = normalize_items(st.session_state.shopping_items)

    if st.session_state.shopping_items:
        df_basket = pd.DataFrame(st.session_state.shopping_items)
        df_basket["food name"] = df_basket["key"].map(lambda k: family_labels.get(k, ""))
        df_basket["remove"] = False

        st.markdown("### Your basket")
        edited = st.data_editor(
            df_basket,
            use_container_width=True,
            hide_index=True,
            column_config={
                "key": st.column_config.TextColumn("key", disabled=True),
                "food name": st.column_config.TextColumn("food name", disabled=True),
                "qty": st.column_config.NumberColumn("qty", min_value=1, step=1),
                "remove": st.column_config.CheckboxColumn("remove"),
            },
            key="basket_editor",
        )

        colA, colB, colC, colD = st.columns([1, 1, 1.5, 1.5])
        with colA:
            if st.button("Apply changes", use_container_width=True):
                new_items = []
                for _, r in edited.iterrows():
                    if bool(r.get("remove")):
                        continue
                    new_items.append({"key": str(r["key"]).strip(), "qty": int(r["qty"])})
                st.session_state.shopping_items = normalize_items(new_items)
                st.rerun()

        with colB:
            if st.button("Clear basket", use_container_width=True):
                st.session_state.shopping_items = []
                st.rerun()

        shopping_payload = {"items": st.session_state.shopping_items}

        with colC:
            st.download_button(
                "Download shopping_list.json",
                data=json.dumps(shopping_payload, ensure_ascii=False, indent=2).encode("utf-8"),
                file_name="shopping_list.json",
                mime="application/json",
                use_container_width=True,
            )

        with colD:
            df_basket_export = pd.DataFrame(st.session_state.shopping_items).copy()
            df_basket_export["food_name"] = df_basket_export["key"].map(lambda k: family_labels.get(k, ""))
            st.download_button(
                "Download basket.csv",
                data=df_to_csv_bytes(df_basket_export[["key", "food_name", "qty"]]),
                file_name="basket.csv",
                mime="text/csv",
                use_container_width=True,
            )
    else:
        st.info("Basket is empty. Add an item above.")
        shopping_payload = {"items": []}

    # --- compare ---
    st.divider()
    st.markdown("### Compare")

    mode = st.radio(
        "Mode",
        ["Single store", "Split basket (cheapest per item)", "Split + penalty per extra store"],
        horizontal=True,
    )
    penalty_eur = 0.0
    if mode == "Split + penalty per extra store":
        penalty_eur = st.number_input(
            "Penalty per extra store (€)",
            min_value=0.0,
            value=2.0,
            step=0.5,
        )

    compare_btn = st.button(
        "Compare now",
        type="primary",
        use_container_width=True,
        disabled=not bool(st.session_state.shopping_items),
    )

    def cost_key(row: dict) -> float:
        v = row.get("eur_per_unit")
        if v is None:
            v = row.get("price_eur")
        try:
            return float(v)
        except Exception:
            return float("inf")

    def render_single_store(res_dict: dict):
        items_sorted = sorted(
            res_dict.items(),
            key=lambda kv: (len(kv[1].get("missing", [])), kv[1].get("total_eur", 10**9)),
        )
        for store, info in items_sorted:
            st.markdown(
                f"### {store} — total **{info['total_eur']:.2f} €**  (missing: {len(info.get('missing', []))})"
            )
            if info.get("chosen"):
                dfc = pd.DataFrame(info["chosen"]).rename(
                    columns={"price_eur": "price (€)", "eur_per_unit": "€/unit"}
                )
                cols = [c for c in ["key", "qty", "label", "pack", "price (€)", "€/unit", "unit", "date", "url"] if c in dfc.columns]
                st.dataframe(dfc[cols], use_container_width=True, hide_index=True)
            if info.get("missing"):
                st.warning("Missing: " + ", ".join(info["missing"]))

    def compute_split_plan(res_dict: dict, payload: dict) -> tuple[list[dict], list[str]]:
        candidates: dict[str, list[tuple[str, dict]]] = {}
        for store, info in res_dict.items():
            for row in info.get("chosen", []):
                k = str(row.get("key", "")).strip()
                if k:
                    candidates.setdefault(k, []).append((store, row))

        missing: list[str] = []
        chosen: list[dict] = []
        for it in payload.get("items", []):
            k = str(it.get("key", "")).strip()
            qty = int(it.get("qty", 1))
            opts = candidates.get(k, [])
            if not opts:
                missing.append(f"{k} x{qty}")
                continue
            best_store, best_row = min(opts, key=lambda sr: cost_key(sr[1]))
            row_out = dict(best_row)
            row_out["store"] = best_store
            chosen.append(row_out)
        return chosen, missing

    def render_split_plan(chosen_rows: list[dict], missing: list[str], penalty: float = 0.0):
        if missing:
            st.warning("Missing overall (no store has price): " + ", ".join(missing))
        if not chosen_rows:
            st.info("No selectable items.")
            return

        total = 0.0
        stores_used: dict[str, list[dict]] = {}
        for r in chosen_rows:
            stores_used.setdefault(r["store"], []).append(r)
            total += float(r.get("price_eur", 0.0)) * int(r.get("qty", 1))

        extra_stores = max(0, len(stores_used) - 1)
        total_with_penalty = total + extra_stores * penalty

        header = f"### Split plan — base **{total:.2f} €**"
        if penalty > 0:
            header += f" + penalty **{extra_stores}×{penalty:.2f} €** = **{total_with_penalty:.2f} €**"
        header += f"  (stores used: {len(stores_used)})"
        st.markdown(header)

        for store, rows in sorted(stores_used.items(), key=lambda kv: kv[0]):
            st.markdown(f"#### {store}")
            dfc = pd.DataFrame(rows).rename(columns={"price_eur": "price (€)", "eur_per_unit": "€/unit"})
            cols = [c for c in ["store", "key", "qty", "label", "pack", "price (€)", "€/unit", "unit", "date", "url"] if c in dfc.columns]
            st.dataframe(dfc[cols], use_container_width=True, hide_index=True)

    if compare_btn:
        res = svc.compare_list(shopping_payload)

        st.markdown("### Exports")
        best_store, best_info = min(
            res.items(),
            key=lambda kv: (len(kv[1].get("missing", [])), kv[1].get("total_eur", 10**9)),
        )

        df_best = pd.DataFrame(best_info.get("chosen", [])).copy()
        if not df_best.empty:
            cols = [c for c in ["key", "qty", "label", "pack", "price_eur", "eur_per_unit", "unit", "date", "url"] if c in df_best.columns]
            df_best = df_best[cols]
            st.download_button(
                f"Download CSV (best single-store: {best_store})",
                data=df_to_csv_bytes(df_best),
                file_name=f"compare_single_{best_store}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        chosen_split, missing_split = compute_split_plan(res, shopping_payload)
        df_split = pd.DataFrame(chosen_split).copy()
        if not df_split.empty:
            cols = [c for c in ["store", "key", "qty", "label", "pack", "price_eur", "eur_per_unit", "unit", "date", "url"] if c in df_split.columns]
            df_split = df_split[cols]
            st.download_button(
                "Download CSV (split plan)",
                data=df_to_csv_bytes(df_split),
                file_name="compare_split.csv",
                mime="text/csv",
                use_container_width=True,
            )

        if mode == "Single store":
            render_single_store(res)
        elif mode == "Split basket (cheapest per item)":
            render_split_plan(chosen_split, missing_split, penalty=0.0)
        else:
            render_split_plan(chosen_split, missing_split, penalty=penalty_eur)
            st.info(
                f"Best single-store baseline: **{best_store}** — "
                f"{best_info['total_eur']:.2f} € (missing: {len(best_info.get('missing', []))})"
            )


with tab3:
    render_tab3()

# ===================== TAB 4: ADD ITEMS =====================
def render_tab4():
    st.subheader("Add items (track-url)")
    st.caption("UI equivalent of CLI: track-url + scrape-all + sync.")

    default_key = families[0] if families else ""
    app_path = str(ROOT / "app.py")
    schema_path_local = str(ROOT / "schema.sql")

    STORE_OPTIONS = ["Spar", "Mercator", "LIDL", "Hofer"]
    SCRAPER_BY_STORE = {
        "Spar": "spar",
        "Mercator": "mercator",
        "LIDL": "lidl",
        "Hofer": "hofer",
    }

    # --- Track URL ---
    with st.form("track_url_form", clear_on_submit=False):
        c1, c2 = st.columns([2, 1])

        with c1:
            store_name = st.selectbox("Store", STORE_OPTIONS, index=0)

        scraper = SCRAPER_BY_STORE[store_name]

        with c2:
            size = st.number_input("Size", min_value=0.0, value=1.0, step=0.1)

        c4, c5, c6 = st.columns([2, 2, 1])
        with c4:
            url = st.text_input("URL", placeholder="https://...").strip()
        with c5:
            key = st.text_input("Key", value=default_key, placeholder="npr. olive_oil").strip()
        with c6:
            unit = st.selectbox("Unit", ["l", "ml", "kg", "g", "pcs"], index=0)

        label = st.text_input("Food name", placeholder='npr. "Trajno mleko 3.5%"').strip()
        store_label = st.text_input("Brand (optional)", placeholder="npr. Alpsko mleko 3.5%").strip() or None

        submitted = st.form_submit_button("Track URL", use_container_width=True)

    if submitted:
        try:
            with transaction(conn):
                store_id = store_get_or_create(conn, store_name)
                canonical_id = canonical_upsert(conn, key, label, float(size), unit)
                store_item_id = store_item_upsert_mapping(
                    conn,
                    store_id=store_id,
                    canonical_item_id=canonical_id,
                    url=url,
                    scraper=scraper,
                    label_override=store_label,
                )

            pack = f"{float(size):g}{unit}"
            extra = f" brand={store_label!r}" if store_label else ""
            st.success(
                f"OK: tracked url for store_item_id={store_item_id} "
                f"store={store_name} key={key} pack={pack}{extra}"
            )
            st.session_state["last_track_key"] = key
            st.cache_data.clear()
            st.rerun()

        except Exception as e:
            st.error(str(e))

    # --- Scrape-all ---
    st.divider()
    st.markdown("### Scrape (scrape-all)")

    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    with c1:
        scrape_date = st.text_input(
            "Observed on (optional)",
            value="",
            placeholder="YYYY-MM-DD (empty = today)",
        ).strip()
    with c2:
        insecure_ssl = st.toggle("Insecure SSL", value=False)
    with c3:
        fail_on_error = st.toggle("Fail on error", value=False)
    with c4:
        show_cmd_scrape = st.toggle("Show cmd", value=False)

    can_scrape = Path(app_path).exists() and Path(schema_path_local).exists() and Path(db_path).exists()
    if not can_scrape:
        st.warning(
            "Cannot run scrape-all because one of these paths is missing:\n"
            f"- app.py: {app_path}\n"
            f"- schema.sql: {schema_path_local}\n"
            f"- db: {db_path}"
        )

    if st.button("Scrape all now", type="primary", use_container_width=True, disabled=not can_scrape):
        cmd = [
            sys.executable,
            app_path,
            "scrape-all",
            "--db", db_path,
            "--schema", schema_path_local,
        ]
        if scrape_date:
            cmd += ["--date", scrape_date]
        if insecure_ssl:
            cmd += ["--insecure-ssl"]
        if fail_on_error:
            cmd += ["--fail-on-error"]

        if show_cmd_scrape:
            st.code(" ".join(cmd))

        with st.spinner("Running scrape-all..."):
            res = subprocess.run(cmd, capture_output=True, text=True)

        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()

        if res.returncode == 0:
            st.success("Scrape finished ✅")
            if out:
                st.code(out)
            if err:
                st.code(err)
            st.cache_data.clear()
        else:
            st.error(f"Scrape failed ❌ (exit code {res.returncode})")
            if out:
                st.code(out)
            if err:
                st.code(err)

    # --- Sync (git pull) ---
    st.divider()
    st.markdown("### Sync (git pull)")

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        discard_db = st.toggle("Discard DB", value=True)
    with c2:
        show_db = st.toggle("Show DB latest day", value=True)
    with c3:
        show_cmd_sync = st.toggle("Show sync cmd", value=False)

    can_sync = Path(app_path).exists() and Path(schema_path_local).exists() and Path(".git").exists()
    if not can_sync:
        st.warning(
            "Cannot run sync because:\n"
            f"- app.py exists? {Path(app_path).exists()}\n"
            f"- schema.sql exists? {Path(schema_path_local).exists()}\n"
            f"- .git exists? {Path('.git').exists()}"
        )

    if st.button("Sync now (git pull)", use_container_width=True, disabled=not can_sync):
        cmd = [
            sys.executable,
            app_path,
            "sync",
            "--db", db_path,
            "--schema", schema_path_local,
        ]
        if show_db:
            cmd += ["--show-db"]
        if discard_db:
            cmd += ["--discard-db"]

        if show_cmd_sync:
            st.code(" ".join(cmd))

        with st.spinner("Running sync (git pull)..."):
            res = subprocess.run(cmd, capture_output=True, text=True)

        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()

        if res.returncode == 0:
            st.success("Sync finished ✅")
            if out:
                st.code(out)
            if err:
                st.code(err)
            st.cache_data.clear()
        else:
            st.error(f"Sync failed ❌ (exit code {res.returncode})")
            if out:
                st.code(out)
            if err:
                st.code(err)

    # --- Preview mappings ---
    st.divider()
    st.markdown("### Preview: mappings for a key")

    key_prefill = st.session_state.get("last_track_key", default_key)
    key_show = st.text_input("Key to preview", value=key_prefill).strip()

    df_prev = pd.DataFrame()
    if key_show:
        rows = conn.execute(
            """
            SELECT
              si.id as store_item_id,
              s.name as store,
              ci.family_key,
              ci.label as canonical_label,
              ci.size,
              ci.unit,
              si.scraper,
              si.url,
              si.label_override
            FROM store_item si
            JOIN store s ON s.id = si.store_id
            JOIN canonical_item ci ON ci.id = si.canonical_item_id
            WHERE ci.family_key = ?
            ORDER BY s.name, ci.size, ci.unit
            """,
            (key_show,),
        ).fetchall()
        df_prev = pd.DataFrame([dict(r) for r in rows])

    if df_prev.empty:
        st.info("No mappings for this key yet (or key is empty).")
    else:
        df_show = df_prev.rename(
            columns={
                "canonical_label": "food name",
                "label_override": "brand",
                "family_key": "key",
            }
        ).copy()

        cols = [
            "store_item_id",
            "store",
            "key",
            "food name",
            "brand",
            "size",
            "unit",
            "scraper",
            "url",
        ]
        cols = [c for c in cols if c in df_show.columns]
        st.dataframe(df_show[cols], use_container_width=True, hide_index=True)

    # --- Delete mapping ---
    st.divider()
    st.markdown("### Delete mapping (store_item)")
    st.caption("Deletes the selected store_item row (observations cascade).")

    if df_prev.empty:
        st.info("No mappings to delete for this key.")
    else:
        df_del = df_prev.copy()
        df_del["display"] = (
            df_del["store_item_id"].astype(str)
            + " • " + df_del["store"].astype(str)
            + " • " + df_del["canonical_label"].astype(str)
            + " • " + df_del["size"].astype(str)
            + df_del["unit"].astype(str)
        )

        choice = st.selectbox("Select mapping to delete", df_del["display"].tolist(), key="delete_choice")
        delete_id = int(choice.split(" • ")[0])

        confirm = st.checkbox("I understand this will delete observations for this mapping.", value=False)
        if st.button("Delete selected mapping", type="primary", disabled=not confirm):
            try:
                delete_store_item(conn, delete_id)
                st.cache_data.clear()
                st.success(f"Deleted store_item_id={delete_id}")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    # --- Delete canonical pack ---
    st.divider()
    st.markdown("### Delete product (canonical pack)")
    st.caption("Deletes the canonical pack and cascades to store mappings + observations.")

    if not key_show:
        st.info("Enter a key above to delete a product.")
    else:
        canon_rows = conn.execute(
            """
            SELECT id, family_key, label, size, unit
            FROM canonical_item
            WHERE family_key = ?
            ORDER BY size, unit
            """,
            (key_show,),
        ).fetchall()

        df_canon = pd.DataFrame([dict(r) for r in canon_rows])

        if df_canon.empty:
            st.info("No canonical packs found for this key.")
        else:
            df_canon["display"] = (
                df_canon["id"].astype(str)
                + " • " + df_canon["label"].astype(str)
                + " • " + df_canon["size"].astype(str)
                + df_canon["unit"].astype(str)
            )

            canon_choice = st.selectbox(
                "Select canonical pack to delete",
                df_canon["display"].tolist(),
                key="canon_delete_choice",
            )
            canon_id = int(canon_choice.split(" • ")[0])

            confirm2 = st.checkbox(
                "I understand this will delete ALL store mappings + observations for this pack.",
                value=False,
            )
            if st.button("Delete canonical pack", type="primary", disabled=not confirm2):
                try:
                    delete_canonical_item(conn, canon_id)
                    st.success(f"Deleted canonical_item_id={canon_id}")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(str(e))

def delete_canonical_item(conn: sqlite3.Connection, canonical_item_id: int) -> None:
    conn.execute("DELETE FROM canonical_item WHERE id = ?", (int(canonical_item_id),))
    conn.commit()

with tab4:
    render_tab4()

with tab5:
    st.title("Help")
    st.caption("A quick onboarding guide for first-time users.")

    st.markdown("""
## What this app does
**Price Tracker V2** tracks product prices across stores (via product URLs), stores daily observations,
and helps you:
- see **latest prices** (and the cheapest store)
- view **history** and price **trend**
- compare a **shopping basket** across stores (single-store vs split plan)

---

## Quick start (first time)
Follow these steps once:

### 1) Make sure the database exists
- In the left sidebar (**Settings**) check **DB path** (default: `data/prices.sqlite`).
- If the DB file is missing, click **Create empty DB (init schema)**.

### 2) Add your first product (Track URL)
Go to **Add items** tab and fill the form:

- **store**: Human-friendly name (e.g. `Mercator`, `Spar`, `LIDL`)
- **scraper**: The scraper key (e.g. `mercator`, `spar`, `lidl`)
- **url**: Product page URL
- **key**: Family key (group name), e.g. `milk_35`, `olive_oil`
- **size + unit**: Pack size (e.g. `1` + `l`, `0.75` + `l`)
- **food name**: Canonical product name (shared across stores), e.g. `Trajno mleko 3.5%`
- **brand (optional)**: Store-specific name/brand, e.g. `Alpsko mleko`

Click **Track URL**.

### 3) Scrape prices
Still in **Add items**, click **Scrape all now**.
That will fetch prices and write observations into the DB.
After that press Sync now (git pull) button to update data.

✅ After this, **Latest** and **History** will show data.

---

## How to use each tab

### Latest
Use this when you want a fast answer: “Where is it cheapest right now?”
- Select a **product key**
- You’ll see a table of current prices per store
- The green box highlights the **cheapest** offer (prefers €/unit when available)

Tip: If you see missing prices, run **Scrape all now**.

### History
Use this when you want to understand price changes over time.
- Select **product key**
- Select **brand** (if you tracked multiple brands/labels under the same key)
- Use **Stores** filter and **Date range** slider
- You get:
  - **Trend table** (numbers like CLI)
  - **Chart** of the same metric

Tip: If you have only **1 date**, the date slider is disabled (that’s normal).

### Shopping list
Use this when you have a “basket” and you want the best plan.
- Build the basket by adding items (key + quantity)
- Choose compare mode:
  - **Single store**: total per store (simple)
  - **Split (cheapest per item)**: each item from the cheapest store
  - **Split + penalty**: adds a cost per extra store (time/fuel)
- Export:
  - `shopping_list.json` (to share/reuse)
  - `basket.csv`
  - compare CSVs (single-store and split plan)

### Add items
This is the “admin tab” to maintain your DB.

You can:
- **Track URL** (add/update a product mapping)
- **Scrape all now** (collect today’s prices)
- **Sync now** (git pull latest DB from repo if you use CI)
- Preview existing mappings for a key
- Delete:
  - **Delete mapping (store_item)** → removes that store URL mapping + observations
  - **Delete canonical pack** → removes the canonical product pack and cascades to mappings + observations

---

## Concepts (so labels make sense)
- **key** = family group (e.g. `milk_35`). Many stores can map to the same key.
- **food name** = canonical name you control (clean and consistent across stores)
- **brand** = store-specific label (optional, for distinguishing brands)
- **mapping** = a tracked URL in a store (store_item)
- **observation** = a daily price record (price_observation)

---

## Common workflows

### Add a new store for an existing product
1) Go to **Add items**
2) Use the same **key**, same **size/unit**, same **food name**
3) Change **store**, **scraper**, and **url**
4) Click **Track URL**
5) Click **Scrape all now**

### Track two different brands under the same key
Example: `olive_oil` for multiple brands
- Keep **key** the same (olive_oil)
- Keep **food name** general (e.g. “Olivno olje”)
- Use **brand** to distinguish (Monini, Bertolli, …)
Then in **History** you select the brand you want.

### Reset everything and start clean
Use sidebar **Reset DB** (Danger zone). This deletes the SQLite file and recreates an empty DB.

---

## Troubleshooting

### “No families found yet”
You have no canonical items yet.
Go to **Add items → Track URL** and add the first product.

### I tracked a URL but Latest is empty
You likely didn’t scrape yet.
Go to **Add items → Scrape all now**.

### Scrape fails
Check:
- scraper key is correct (`spar`, `mercator`, `lidl`, …)
- URL opens in browser
- network/SSL issues (try `--insecure-ssl` only for dev)
Open the scrape output block to see the error.

### I deleted something but it still appears
- Deleting a **mapping** removes only that store URL.
- The **canonical product** can remain without mappings.
Use **Delete canonical pack** if you want it fully removed.

---

## Suggested “first 10 minutes” checklist
- [ ] Create DB (if missing)
- [ ] Add 1 product URL for 1 store
- [ ] Scrape all
- [ ] Check Latest
- [ ] Add second store URL for same key
- [ ] Scrape all
- [ ] Sync data
- [ ] Check History and Shopping list
""")

    st.info("Tip: If you're unsure what to do next, go to **Add items** and make sure at least one URL is tracked, then run **Scrape all now**.")