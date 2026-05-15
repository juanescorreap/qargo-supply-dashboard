import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime

st.set_page_config(
    page_title="Qargo Coffee — Supply Dashboard",
    page_icon="☕",
    layout="wide",
)

BASE_DIR = Path(__file__).parent
MIN_RELIABLE_WEEKS = 8
EXCLUDED_STORES = ['183', 'Nashua']

CATEGORY_LABELS = {
    ("Coffee", "g"):              "Espresso & Ground Coffee (kg)",
    ("Coffee", "ml"):             "Cold Brew & Drip Coffee (L)",
    ("Dairy", "ml"):              "Traditional Dairy — Liquid (L)",
    ("Dairy", "g"):               "Traditional Dairy — Solid (kg)",
    ("Specialty Milk", "ml"):     "Plant-Based Milk (L)",
    ("Syrups", "pump"):           "Syrups — Total (pumps)",
    ("Syrups", "g"):              "Syrups — Total (kg)",
    ("Plastic goods", "cup"):     "Cups — Total (units)",
    ("Plastic goods", "lid"):     "Lids (units)",
    ("Plastic goods", "sleeve"):  "Sleeves (units)",
    ("Plastic goods", "straw"):   "Straws (units)",
    ("Plastic goods", "napkin"):  "Napkins (units)",
}

# Orden lógico de columnas en vista agregada: café → lácteos → specialty → syrups → packaging
COLUMN_ORDER_AGG = [
    "Espresso & Ground Coffee (kg)",
    "Cold Brew & Drip Coffee (L)",
    "Traditional Dairy — Liquid (L)",
    "Traditional Dairy — Solid (kg)",
    "Plant-Based Milk (L)",
    "Syrups — Total (pumps)",
    "Cups — Total (units)",
    "Lids (units)",
    "Sleeves (units)",
    "Straws (units)",
    "Napkins (units)",
]

UNIT_DISPLAY = {
    ("Coffee", "g"):              "kg",
    ("Coffee", "ml"):             "L",
    ("Dairy", "ml"):              "L",
    ("Dairy", "g"):               "kg",
    ("Specialty Milk", "ml"):     "L",
    ("Syrups", "pump"):           "pumps",
    ("Syrups", "g"):              "kg",
    ("Plastic goods", "cup"):     "units",
    ("Plastic goods", "lid"):     "units",
    ("Plastic goods", "sleeve"):  "units",
    ("Plastic goods", "straw"):   "units",
    ("Plastic goods", "napkin"):  "units",
}

SCALE = {
    ("Coffee", "g"):          1 / 1000,
    ("Coffee", "ml"):         1 / 1000,
    ("Dairy", "ml"):          1 / 1000,
    ("Dairy", "g"):           1 / 1000,
    ("Specialty Milk", "ml"): 1 / 1000,
}


@st.cache_data
def load_data():
    detail  = pd.read_csv(BASE_DIR / "consumption_by_store_week.csv")
    summary = pd.read_csv(BASE_DIR / "consumption_summary.csv")

    # Backwards-compat: add subcategory if old CSV
    for df in (detail, summary):
        if 'subcategory' not in df.columns:
            df['subcategory'] = ''

    # Scale conversions
    for (cat, unit), factor in SCALE.items():
        mask_d = (detail["category"] == cat) & (detail["unit"] == unit)
        detail.loc[mask_d, "consumption_with_waste"] *= factor
        detail.loc[mask_d, "consumption"] *= factor

        mask_s = (summary["category"] == cat) & (summary["unit"] == unit)
        summary.loc[mask_s, "avg_weekly_consumption"] *= factor

    # Labels
    detail["label"] = detail.apply(
        lambda r: CATEGORY_LABELS.get((r["category"], r["unit"]), f"{r['category']} ({r['unit']})"), axis=1
    )
    summary["label"] = summary.apply(
        lambda r: CATEGORY_LABELS.get((r["category"], r["unit"]), f"{r['category']} ({r['unit']})"), axis=1
    )
    summary["unit_display"] = summary.apply(
        lambda r: UNIT_DISPLAY.get((r["category"], r["unit"]), r["unit"]), axis=1
    )

    # Reliability flag
    reliable = summary.groupby("store")["weeks_with_data"].max()
    summary["reliable"] = summary["store"].map(reliable) >= MIN_RELIABLE_WEEKS
    detail["reliable"]  = detail["store"].map(reliable)  >= MIN_RELIABLE_WEEKS

    # Disaggregated labels (subcategory-level column names for overview)
    def _disagg_label(row):
        cat, sub, unit = row["category"], row["subcategory"] or "", row["unit"]
        u = "kg" if unit == "g" else ("L" if unit == "ml" else unit)
        if cat == "Coffee" or not sub:
            return CATEGORY_LABELS.get((cat, unit), f"{cat} ({u})")
        if cat in ("Dairy", "Specialty Milk"):
            return f"{sub} ({u})"
        if cat == "Syrups":
            return f"{sub} (pumps)"
        if cat == "Plastic goods":
            if sub.startswith("cup_"):
                return "Cup " + sub.replace("cup_", "")
            return {"lid": "Lids", "sleeve": "Sleeves", "straw": "Straws",
                    "napkin": "Napkins", "bag": "Bags"}.get(sub, sub.capitalize())
        return CATEGORY_LABELS.get((cat, unit), f"{cat} ({u})")

    summary["disagg_label"] = summary.apply(_disagg_label, axis=1)
    # Map disagg_label → category for column ordering
    disagg_label_cat = dict(zip(summary["disagg_label"], summary["category"]))

    # Bev vs Food stats
    bev_food_path = BASE_DIR / "bev_food_stats.csv"
    bev_food = pd.read_csv(bev_food_path) if bev_food_path.exists() else None

    return detail, summary, disagg_label_cat, bev_food


detail_df, summary_df, disagg_label_cat, bev_food_df = load_data()
all_stores = sorted(detail_df["store"].unique())
unreliable_stores = summary_df[~summary_df["reliable"]]["store"].unique().tolist()


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://placehold.co/200x60/1a1a1a/ffffff?text=Qargo+Coffee", width=200)
    st.markdown("## Filters")

    store_options = ["All stores"] + all_stores
    selected_store = st.selectbox("Store", store_options)

    all_weeks = sorted(detail_df["week"].unique())

    def _fmt_week(w):
        d = datetime.strptime(w.split("/")[0], "%Y-%m-%d")
        return d.strftime("%b %-d")

    week_start, week_end = st.select_slider(
        "Week range",
        options=all_weeks,
        value=(all_weeks[0], all_weeks[-1]),
        format_func=_fmt_week,
    )

    show_waste   = st.toggle("Include waste (7%)", value=True)
    only_reliable = st.toggle("Reliable stores only (≥8 weeks)", value=True)

    st.divider()
    st.caption("**Excluded stores (permanent):**")
    for exc in EXCLUDED_STORES:
        st.caption(f"- {exc}")

    if unreliable_stores:
        st.warning(
            f"**{len(unreliable_stores)} store(s) with insufficient data:**\n\n"
            + "\n".join(f"- {s.replace('Qargo Coffee ', '')}" for s in unreliable_stores)
        )


# ── Filters ───────────────────────────────────────────────────────────────────
weeks_range = [w for w in all_weeks if week_start <= w <= week_end]
det = detail_df[detail_df["week"].isin(weeks_range)].copy()
if selected_store != "All stores":
    det = det[det["store"] == selected_store]
if only_reliable:
    det = det[det["reliable"]]

col_val = "consumption_with_waste" if show_waste else "consumption"

sum_filt = summary_df.copy()
if selected_store != "All stores":
    sum_filt = sum_filt[sum_filt["store"] == selected_store]
if only_reliable:
    sum_filt = sum_filt[sum_filt["reliable"]]


# ── Header ────────────────────────────────────────────────────────────────────
st.title("☕ Supply Consumption Dashboard")
st.caption(
    f"Data: {len(weeks_range)} week(s) selected  •  "
    f"{'Store: ' + selected_store.replace('Qargo Coffee ', '') if selected_store != 'All stores' else str(det['store'].nunique()) + ' stores'}"
)


# ── KPI Cards ─────────────────────────────────────────────────────────────────
kpi_cats = [
    ("Coffee", "g",          "☕ Espresso (kg)"),
    ("Coffee", "ml",         "🫙 Cold Brew (L)"),
    ("Dairy", "ml",          "🥛 Dairy (L)"),
    ("Plastic goods", "cup", "🥤 Cups"),
]

kpi_cols = st.columns(4)
for col, (cat, unit, label) in zip(kpi_cols, kpi_cats):
    subset = det[(det["category"] == cat) & (det["unit"] == unit)]
    if selected_store == "All stores":
        total = subset.groupby(["store", "week"])[col_val].sum().reset_index()
        value = total[col_val].mean() if not total.empty else 0
        suffix = "/ store·week"
    else:
        total = subset.groupby("week")[col_val].sum().reset_index()
        value = total[col_val].mean() if not total.empty else 0
        suffix = "/ week"
    col.metric(label, f"{value:,.1f}", suffix)

st.divider()


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_overview, tab_coffee, tab_dairy, tab_specialty, tab_syrups, tab_packaging, tab_trend, tab_sales_mix = st.tabs(
    ["Overview", "Coffee", "Dairy", "Specialty Milk", "Syrups", "Packaging", "Trends", "Sales Mix"]
)


# ── Helpers para Overview ─────────────────────────────────────────────────────
def _sort_disagg_cols(cols):
    """Ordena columnas desagregadas: café → lácteos trad → plant-based → syrups → cups → packaging."""
    CAT_RANK = {"Coffee": 0, "Dairy": 1, "Specialty Milk": 2, "Syrups": 3, "Plastic goods": 4, "Paper goods": 5}
    CUP_RANK = {"Cup 12oz": 0, "Cup 16oz": 1, "Cup 20oz": 2, "Cup 24oz": 3}
    PKG_RANK = {"Lids": 0, "Sleeves": 1, "Straws": 2, "Napkins": 3, "Bags": 4}

    def _key(c):
        cat = disagg_label_cat.get(c, "Z")
        r = CAT_RANK.get(cat, 9)
        if c in CUP_RANK:
            return (r, CUP_RANK[c], c)
        if c in PKG_RANK:
            return (r, 10 + PKG_RANK[c], c)
        return (r, 0, c)

    return sorted(cols, key=_key)


def _sort_agg_cols(cols):
    """Ordena columnas agregadas según COLUMN_ORDER_AGG; el resto al final."""
    known = [c for c in COLUMN_ORDER_AGG if c in cols]
    rest  = sorted(c for c in cols if c not in known)
    return known + rest


# ── Overview ──────────────────────────────────────────────────────────────────
with tab_overview:
    view_mode = st.radio(
        "Detail level",
        ["Main categories", "Ingredient breakdown"],
        horizontal=True,
    )

    store_col = "store_short"

    if view_mode == "Main categories":
        pivot = (
            sum_filt
            .groupby(["store", "label"])["avg_weekly_consumption"]
            .sum()
            .reset_index()
        )
        pivot[store_col] = pivot["store"].str.replace("Qargo Coffee ", "", regex=False)
        table = pivot.pivot_table(
            index=store_col, columns="label",
            values="avg_weekly_consumption", aggfunc="sum",
        ).round(1)
        ordered_cols = _sort_agg_cols(table.columns.tolist())
        table = table[[c for c in ordered_cols if c in table.columns]]
        table.columns.name = None
        st.dataframe(table, use_container_width=True)

    else:
        pivot = (
            sum_filt
            .groupby(["store", "disagg_label"])["avg_weekly_consumption"]
            .sum()
            .reset_index()
        )
        pivot[store_col] = pivot["store"].str.replace("Qargo Coffee ", "", regex=False)
        table = pivot.pivot_table(
            index=store_col, columns="disagg_label",
            values="avg_weekly_consumption", aggfunc="sum",
        ).round(1)
        ordered_cols = _sort_disagg_cols(table.columns.tolist())
        table = table[[c for c in ordered_cols if c in table.columns]]
        table.columns.name = None
        st.dataframe(table, use_container_width=True)

    st.caption("Values = weekly average per store (waste included). Excluded stores: " + ", ".join(EXCLUDED_STORES))


# ── Coffee ────────────────────────────────────────────────────────────────────
with tab_coffee:
    c1, c2 = st.columns(2)
    for col_ui, (cat, unit, title) in zip(
        [c1, c2],
        [("Coffee", "g", "Espresso / Ground Coffee (kg/week)"),
         ("Coffee", "ml", "Cold Brew / Drip Coffee (L/week)")],
    ):
        subset = det[(det["category"] == cat) & (det["unit"] == unit)]
        avg = (
            subset.groupby(["store", "week"])[col_val].sum()
            .groupby("store").mean()
            .reset_index()
            .rename(columns={col_val: "value"})
        )
        avg["store_short"] = avg["store"].str.replace("Qargo Coffee ", "", regex=False)
        avg = avg.sort_values("value", ascending=False)

        fig = px.bar(
            avg, x="store_short", y="value", title=title,
            labels={"store_short": "", "value": title.split("(")[1].rstrip(")")},
            color="value", color_continuous_scale="Oranges",
        )
        fig.update_layout(showlegend=False, coloraxis_showscale=False, height=400)
        fig.update_xaxes(tickangle=-40)
        col_ui.plotly_chart(fig, use_container_width=True)


# ── Dairy ─────────────────────────────────────────────────────────────────────
with tab_dairy:
    st.subheader("Traditional Dairy — breakdown by milk type")

    dairy_sub = det[(det["category"] == "Dairy") & (det["unit"] == "ml")].copy()

    # Bar by store (total)
    avg_total = (
        dairy_sub.groupby(["store", "week"])[col_val].sum()
        .groupby("store").mean()
        .reset_index()
        .rename(columns={col_val: "value"})
    )
    avg_total["store_short"] = avg_total["store"].str.replace("Qargo Coffee ", "", regex=False)
    avg_total = avg_total.sort_values("value", ascending=False)

    fig_total = px.bar(
        avg_total, x="store_short", y="value",
        title="Traditional Dairy — weekly average (L)",
        labels={"store_short": "", "value": "Liters / week"},
        color="value", color_continuous_scale="Blues",
    )
    fig_total.update_layout(coloraxis_showscale=False, height=400)
    fig_total.update_xaxes(tickangle=-40)
    st.plotly_chart(fig_total, use_container_width=True)

    # Breakdown by milk type (stacked bar)
    if dairy_sub["subcategory"].any():
        st.subheader("Breakdown by milk type")
        avg_type = (
            dairy_sub.groupby(["store", "week", "subcategory"])[col_val].sum()
            .groupby(["store", "subcategory"]).mean()
            .reset_index()
            .rename(columns={col_val: "value"})
        )
        avg_type["store_short"] = avg_type["store"].str.replace("Qargo Coffee ", "", regex=False)

        fig_type = px.bar(
            avg_type.sort_values("value", ascending=False),
            x="store_short", y="value", color="subcategory",
            barmode="stack",
            title="Dairy by milk type — weekly average (L)",
            labels={"store_short": "", "value": "Liters / week", "subcategory": "Milk type"},
            height=420,
        )
        fig_type.update_xaxes(tickangle=-40)
        st.plotly_chart(fig_type, use_container_width=True)


# ── Specialty Milk ────────────────────────────────────────────────────────────
with tab_specialty:
    st.subheader("Specialty Milk (plant-based) — breakdown by type")

    spec_sub = det[(det["category"] == "Specialty Milk") & (det["unit"] == "ml")].copy()

    if spec_sub.empty:
        st.info("No Specialty Milk data in the selected range. Re-run the analysis script after updating the classification file.")
    else:
        avg_spec = (
            spec_sub.groupby(["store", "week", "subcategory"])[col_val].sum()
            .groupby(["store", "subcategory"]).mean()
            .reset_index()
            .rename(columns={col_val: "value"})
        )
        avg_spec["store_short"] = avg_spec["store"].str.replace("Qargo Coffee ", "", regex=False)

        fig_spec = px.bar(
            avg_spec.sort_values("value", ascending=False),
            x="store_short", y="value", color="subcategory",
            barmode="stack",
            title="Specialty Milk — weekly average by type (L)",
            labels={"store_short": "", "value": "Liters / week", "subcategory": "Milk type"},
            color_discrete_sequence=px.colors.qualitative.Pastel,
            height=440,
        )
        fig_spec.update_xaxes(tickangle=-40)
        st.plotly_chart(fig_spec, use_container_width=True)

        # Pie chart: share by milk type across all stores
        total_by_type = avg_spec.groupby("subcategory")["value"].sum().reset_index()
        fig_pie = px.pie(
            total_by_type, names="subcategory", values="value",
            title="Specialty Milk — share by type",
            color_discrete_sequence=px.colors.qualitative.Pastel,
        )
        fig_pie.update_traces(textposition='inside', textinfo='percent+label')
        st.plotly_chart(fig_pie, use_container_width=True)


# ── Syrups ────────────────────────────────────────────────────────────────────
with tab_syrups:
    st.subheader("Syrups — consumption by type")

    syr_sub = det[det["category"] == "Syrups"].copy()

    if syr_sub.empty:
        st.info("No Syrups data in the selected range. Re-run the analysis script after updating the classification file.")
    else:
        avg_syr = (
            syr_sub.groupby(["store", "week", "subcategory"])[col_val].sum()
            .groupby(["store", "subcategory"]).mean()
            .reset_index()
            .rename(columns={col_val: "value"})
        )
        avg_syr["store_short"] = avg_syr["store"].str.replace("Qargo Coffee ", "", regex=False)

        fig_syr = px.bar(
            avg_syr.sort_values("value", ascending=False),
            x="store_short", y="value", color="subcategory",
            barmode="stack",
            title="Syrups — weekly average by type (pumps)",
            labels={"store_short": "", "value": "Pumps / week", "subcategory": "Syrup"},
            height=440,
        )
        fig_syr.update_xaxes(tickangle=-40)
        st.plotly_chart(fig_syr, use_container_width=True)

        # Top syrups ranking
        top_syr = (
            syr_sub.groupby("subcategory")[col_val].sum()
            .sort_values(ascending=False)
            .head(10)
            .reset_index()
            .rename(columns={col_val: "total"})
        )
        fig_rank = px.bar(
            top_syr, x="subcategory", y="total",
            title="Top 10 syrups (total consumption)",
            labels={"subcategory": "", "total": "Pumps (total period)"},
            color="total", color_continuous_scale="Purples",
        )
        fig_rank.update_layout(coloraxis_showscale=False, height=380)
        fig_rank.update_xaxes(tickangle=-30)
        st.plotly_chart(fig_rank, use_container_width=True)


# ── Packaging ─────────────────────────────────────────────────────────────────
with tab_packaging:
    st.subheader("Packaging — weekly average by store")

    # Cup breakdown by size
    cup_sub = det[(det["category"] == "Plastic goods") & (det["unit"] == "cup")].copy()
    if not cup_sub.empty and cup_sub["subcategory"].any():
        avg_cups = (
            cup_sub.groupby(["store", "week", "subcategory"])[col_val].sum()
            .groupby(["store", "subcategory"]).mean()
            .reset_index()
            .rename(columns={col_val: "value"})
        )
        avg_cups["store_short"] = avg_cups["store"].str.replace("Qargo Coffee ", "", regex=False)

        # Sort subcategories by size for consistent stacking
        size_order = ["cup_12oz", "cup_16oz", "cup_20oz", "cup_24oz"]
        avg_cups["subcategory"] = pd.Categorical(
            avg_cups["subcategory"],
            categories=[s for s in size_order if s in avg_cups["subcategory"].unique()],
            ordered=True
        )
        avg_cups = avg_cups.sort_values(["store_short", "subcategory"])

        fig_cups = px.bar(
            avg_cups,
            x="store_short", y="value", color="subcategory",
            barmode="stack",
            title="Cups by size — weekly average (units)",
            labels={"store_short": "", "value": "Units / week", "subcategory": "Cup size"},
            color_discrete_map={
                "cup_12oz": "#a8d8ea",
                "cup_16oz": "#4a90d9",
                "cup_20oz": "#1a5276",
                "cup_24oz": "#0a2740",
            },
            height=420,
        )
        fig_cups.update_xaxes(tickangle=-40)
        st.plotly_chart(fig_cups, use_container_width=True)
        st.divider()

    # Other packaging items
    pkg_cats = {"lid": "Lids", "sleeve": "Sleeves", "straw": "Straws", "napkin": "Napkins"}
    pkg_data = []
    for unit, label in pkg_cats.items():
        subset = det[(det["category"] == "Plastic goods") & (det["unit"] == unit)]
        avg = (
            subset.groupby(["store", "week"])[col_val].sum()
            .groupby("store").mean()
            .reset_index()
            .rename(columns={col_val: "value"})
        )
        avg["item"] = label
        pkg_data.append(avg)

    if pkg_data:
        pkg_df = pd.concat(pkg_data)
        pkg_df["store_short"] = pkg_df["store"].str.replace("Qargo Coffee ", "", regex=False)

        fig_pkg = px.bar(
            pkg_df.sort_values("value", ascending=False),
            x="store_short", y="value", color="item",
            barmode="group",
            title="Other packaging — weekly average (units)",
            labels={"store_short": "", "value": "Units / week", "item": ""},
            height=440,
        )
        fig_pkg.update_xaxes(tickangle=-40)
        st.plotly_chart(fig_pkg, use_container_width=True)


# ── Trends ────────────────────────────────────────────────────────────────────
with tab_trend:
    trend_cat_options = {
        "Espresso / Ground Coffee (kg)": ("Coffee", "g"),
        "Cold Brew / Drip Coffee (L)":   ("Coffee", "ml"),
        "Liquid Dairy (L)":              ("Dairy", "ml"),
        "Specialty Milk (L)":            ("Specialty Milk", "ml"),
        "Cups":                          ("Plastic goods", "cup"),
    }
    chosen_label = st.selectbox("Category", list(trend_cat_options.keys()))
    cat, unit = trend_cat_options[chosen_label]

    subset = det[(det["category"] == cat) & (det["unit"] == unit)]
    trend = subset.groupby(["week", "store"])[col_val].sum().reset_index()
    trend["store_short"] = trend["store"].str.replace("Qargo Coffee ", "", regex=False)

    if selected_store != "All stores":
        fig = px.line(
            trend.sort_values("week"), x="week", y=col_val,
            title=f"{chosen_label} — weekly trend",
            labels={"week": "Week", col_val: chosen_label},
            markers=True,
        )
    else:
        fig = px.line(
            trend.sort_values("week"), x="week", y=col_val,
            color="store_short",
            title=f"{chosen_label} — weekly trend by store",
            labels={"week": "Week", col_val: chosen_label, "store_short": "Store"},
            markers=True,
        )
    fig.update_xaxes(tickangle=-40)
    fig.update_layout(height=500)
    st.plotly_chart(fig, use_container_width=True)


# ── Sales Mix ─────────────────────────────────────────────────────────────────
with tab_sales_mix:
    st.subheader("Beverages vs Food & Other")

    if bev_food_df is None:
        st.info(
            "Run `process_sales_LOCAL.py` to generate `bev_food_stats.csv`. "
            "This file is created automatically by the analysis script."
        )
    else:
        total_trans = bev_food_df["transactions"].sum()
        bev_row  = bev_food_df[bev_food_df["revenue_center"] == "Beverages"].iloc[0]
        food_row = bev_food_df[bev_food_df["revenue_center"] == "Food & Other"].iloc[0]

        m1, m2, m3 = st.columns(3)
        m1.metric("Total transactions", f"{total_trans:,}")
        m2.metric("Beverages", f"{bev_row['transactions']:,}", f"{bev_row['percentage']}%")
        m3.metric("Food & Other", f"{food_row['transactions']:,}", f"{food_row['percentage']}%")

        st.divider()

        fig_pie = px.pie(
            bev_food_df,
            names="revenue_center",
            values="transactions",
            title="Transaction distribution — Beverages vs Food & Other",
            color="revenue_center",
            color_discrete_map={
                "Beverages":    "#c0392b",
                "Food & Other": "#2c3e50",
            },
            hole=0.4,
        )
        fig_pie.update_traces(textposition="outside", textinfo="percent+label+value")
        fig_pie.update_layout(height=500)
        st.plotly_chart(fig_pie, use_container_width=True)

        st.caption(
            "⚠️  Tiendas excluidas permanentemente del análisis: "
            + ", ".join(EXCLUDED_STORES)
        )
