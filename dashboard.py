import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path

st.set_page_config(
    page_title="Qargo Coffee — Supply Dashboard",
    page_icon="☕",
    layout="wide",
)

BASE_DIR = Path(__file__).parent

MIN_RELIABLE_WEEKS = 8

CATEGORY_LABELS = {
    ("Coffee", "g"):           "Espresso / Ground Coffee (kg)",
    ("Coffee", "ml"):          "Cold Brew / Drip Coffee (L)",
    ("Dairy", "ml"):           "Liquid Dairy (L)",
    ("Dairy", "g"):            "Solid Dairy (kg)",
    ("Plastic goods", "cup"):  "Cups",
    ("Plastic goods", "lid"):  "Lids",
    ("Plastic goods", "sleeve"): "Sleeves",
    ("Plastic goods", "straw"):  "Straws",
    ("Plastic goods", "napkin"): "Napkins",
}

UNIT_DISPLAY = {
    ("Coffee", "g"):           "kg",
    ("Coffee", "ml"):          "L",
    ("Dairy", "ml"):           "L",
    ("Dairy", "g"):            "kg",
    ("Plastic goods", "cup"):  "units",
    ("Plastic goods", "lid"):  "units",
    ("Plastic goods", "sleeve"): "units",
    ("Plastic goods", "straw"):  "units",
    ("Plastic goods", "napkin"): "units",
}

SCALE = {
    ("Coffee", "g"):  1 / 1000,
    ("Coffee", "ml"): 1 / 1000,
    ("Dairy", "ml"):  1 / 1000,
    ("Dairy", "g"):   1 / 1000,
}


@st.cache_data
def load_data():
    detail = pd.read_csv(BASE_DIR / "consumption_by_store_week.csv")
    summary = pd.read_csv(BASE_DIR / "consumption_summary.csv")

    # Apply scale conversions on detail
    for (cat, unit), factor in SCALE.items():
        mask = (detail["category"] == cat) & (detail["unit"] == unit)
        detail.loc[mask, "consumption_with_waste"] *= factor
        detail.loc[mask, "consumption"] *= factor

    # Apply scale conversions on summary
    for (cat, unit), factor in SCALE.items():
        mask = (summary["category"] == cat) & (summary["unit"] == unit)
        summary.loc[mask, "avg_weekly_consumption"] *= factor

    # Label
    detail["label"] = detail.apply(
        lambda r: CATEGORY_LABELS.get((r["category"], r["unit"]), f"{r['category']} ({r['unit']})"), axis=1
    )
    summary["label"] = summary.apply(
        lambda r: CATEGORY_LABELS.get((r["category"], r["unit"]), f"{r['category']} ({r['unit']})"), axis=1
    )
    summary["unit_display"] = summary.apply(
        lambda r: UNIT_DISPLAY.get((r["category"], r["unit"]), r["unit"]), axis=1
    )

    # Mark unreliable stores
    reliable = summary.groupby("store")["weeks_with_data"].max()
    summary["reliable"] = summary["store"].map(reliable) >= MIN_RELIABLE_WEEKS
    detail["reliable"] = detail["store"].map(reliable) >= MIN_RELIABLE_WEEKS

    return detail, summary


detail_df, summary_df = load_data()
all_stores = sorted(detail_df["store"].unique())
unreliable_stores = summary_df[~summary_df["reliable"]]["store"].unique().tolist()


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://placehold.co/200x60/1a1a1a/ffffff?text=Qargo+Coffee", width=200)
    st.markdown("## Filters")

    store_options = ["All stores"] + all_stores
    selected_store = st.selectbox("Store", store_options)

    all_weeks = sorted(detail_df["week"].unique())
    week_start, week_end = st.select_slider(
        "Week range",
        options=all_weeks,
        value=(all_weeks[0], all_weeks[-1]),
    )

    show_waste = st.toggle("Include waste (7%)", value=True)
    only_reliable = st.toggle("Reliable stores only (≥8 weeks)", value=True)

    st.divider()
    if unreliable_stores:
        st.warning(
            f"**{len(unreliable_stores)} store(s) with insufficient data:**\n\n"
            + "\n".join(f"- {s.replace('Qargo Coffee ', '')}" for s in unreliable_stores)
        )


# ── Filter data ───────────────────────────────────────────────────────────────
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
    ("Coffee", "g",           "☕ Espresso (kg)"),
    ("Coffee", "ml",          "🫙 Cold Brew (L)"),
    ("Dairy", "ml",           "🥛 Dairy (L)"),
    ("Plastic goods", "cup",  "🥤 Cups"),
]

kpi_cols = st.columns(4)
for col, (cat, unit, label) in zip(kpi_cols, kpi_cats):
    subset = det[(det["category"] == cat) & (det["unit"] == unit)]
    if selected_store == "All stores":
        # average per store per week
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
tab_overview, tab_coffee, tab_dairy, tab_packaging, tab_trend = st.tabs(
    ["Overview", "Coffee", "Dairy", "Packaging", "Trends"]
)


# ── TAB: Resumen ──────────────────────────────────────────────────────────────
with tab_overview:
    st.subheader("Weekly average by store")

    pivot = (
        sum_filt.groupby(["store", "label", "unit_display"])["avg_weekly_consumption"]
        .mean()
        .reset_index()
    )
    pivot["store_short"] = pivot["store"].str.replace("Qargo Coffee ", "", regex=False)

    # Table
    table = pivot.pivot_table(
        index="store_short",
        columns="label",
        values="avg_weekly_consumption",
        aggfunc="mean",
    ).round(2)
    st.dataframe(table, use_container_width=True)


# ── TAB: Café ────────────────────────────────────────────────────────────────
with tab_coffee:
    c1, c2 = st.columns(2)

    for col_ui, (cat, unit, title) in zip(
        [c1, c2],
        [("Coffee", "g", "Espresso / Ground Coffee (kg/week)"), ("Coffee", "ml", "Cold Brew / Drip Coffee (L/week)")],
    ):
        subset = det[(det["category"] == cat) & (det["unit"] == unit)]
        avg = (
            subset.groupby(["store", "week"])[col_val].sum()
            .groupby("store").mean()
            .reset_index()
            .rename(columns={col_val: "value", "store": "store"})
        )
        avg["store_short"] = avg["store"].str.replace("Qargo Coffee ", "", regex=False)
        avg = avg.sort_values("value", ascending=False)

        fig = px.bar(
            avg,
            x="store_short",
            y="value",
            title=title,
            labels={"store_short": "", "value": title.split("(")[1].rstrip(")")},
            color="value",
            color_continuous_scale="Oranges",
        )
        fig.update_layout(showlegend=False, coloraxis_showscale=False, height=400)
        fig.update_xaxes(tickangle=-40)
        col_ui.plotly_chart(fig, use_container_width=True)


# ── TAB: Lácteos ──────────────────────────────────────────────────────────────
with tab_dairy:
    subset = det[(det["category"] == "Dairy") & (det["unit"] == "ml")]
    avg = (
        subset.groupby(["store", "week"])[col_val].sum()
        .groupby("store").mean()
        .reset_index()
        .rename(columns={col_val: "value"})
    )
    avg["store_short"] = avg["store"].str.replace("Qargo Coffee ", "", regex=False)
    avg = avg.sort_values("value", ascending=False)

    fig = px.bar(
        avg,
        x="store_short",
        y="value",
        title="Liquid Dairy — weekly average (L)",
        labels={"store_short": "", "value": "Liters / week"},
        color="value",
        color_continuous_scale="Blues",
    )
    fig.update_layout(coloraxis_showscale=False, height=420)
    fig.update_xaxes(tickangle=-40)
    st.plotly_chart(fig, use_container_width=True)


# ── TAB: Packaging ────────────────────────────────────────────────────────────
with tab_packaging:
    pkg_cats = {
        "cup": "Cups",
        "lid": "Lids",
        "sleeve": "Sleeves",
        "straw": "Straws",
        "napkin": "Napkins",
    }

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

        fig = px.bar(
            pkg_df.sort_values("value", ascending=False),
            x="store_short",
            y="value",
            color="item",
            barmode="group",
            title="Packaging — weekly average by store (units)",
            labels={"store_short": "", "value": "Units / week", "item": ""},
            height=480,
        )
        fig.update_xaxes(tickangle=-40)
        st.plotly_chart(fig, use_container_width=True)


# ── TAB: Tendencia ────────────────────────────────────────────────────────────
with tab_trend:
    trend_cat_options = {
        "Espresso / Ground Coffee (kg)": ("Coffee", "g"),
        "Cold Brew / Drip Coffee (L)": ("Coffee", "ml"),
        "Liquid Dairy (L)": ("Dairy", "ml"),
        "Cups": ("Plastic goods", "cup"),
    }
    chosen_label = st.selectbox("Category", list(trend_cat_options.keys()))
    cat, unit = trend_cat_options[chosen_label]

    subset = det[(det["category"] == cat) & (det["unit"] == unit)]
    trend = subset.groupby(["week", "store"])[col_val].sum().reset_index()
    trend["store_short"] = trend["store"].str.replace("Qargo Coffee ", "", regex=False)

    if selected_store != "All stores":
        fig = px.line(
            trend.sort_values("week"),
            x="week",
            y=col_val,
            title=f"{chosen_label} — weekly trend",
            labels={"week": "Week", col_val: chosen_label},
            markers=True,
        )
    else:
        fig = px.line(
            trend.sort_values("week"),
            x="week",
            y=col_val,
            color="store_short",
            title=f"{chosen_label} — weekly trend by store",
            labels={"week": "Week", col_val: chosen_label, "store_short": "Store"},
            markers=True,
        )
    fig.update_xaxes(tickangle=-40)
    fig.update_layout(height=500)
    st.plotly_chart(fig, use_container_width=True)
