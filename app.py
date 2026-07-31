import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from shopify_client import get_access_token, fetch_all_orders, extract_line_items
from sheets_client import get_cost_data, get_overhead_costs

st.set_page_config(page_title="Lønnsomhetsdashboard", page_icon="📊", layout="wide")

# --- Load secrets ---
shopify_cfg = st.secrets["shopify"]
google_cfg = st.secrets["google"]
SHOP = shopify_cfg["shop"]
CLIENT_ID = shopify_cfg["client_id"]
CLIENT_SECRET = shopify_cfg["client_secret"]
SPREADSHEET_ID = google_cfg["spreadsheet_id"]
SERVICE_ACCOUNT_INFO = dict(google_cfg["service_account"])


# --- Data loading (cached) ---
@st.cache_data(ttl=3600)
def load_data():
    # Shopify orders
    token = get_access_token(CLIENT_ID, CLIENT_SECRET, SHOP)
    orders = fetch_all_orders(token, SHOP)
    items_df = pd.DataFrame(extract_line_items(orders))
    items_df["order_date"] = pd.to_datetime(items_df["order_date"])
    items_df["revenue"] = items_df["unit_price"] * items_df["quantity"] - items_df["total_discount"]

    # Cost data from Google Sheets
    costs_df = get_cost_data(SERVICE_ACCOUNT_INFO, SPREADSHEET_ID)

    # Overhead costs
    overhead = get_overhead_costs(SERVICE_ACCOUNT_INFO, SPREADSHEET_ID)

    # MVA (VAT) adjustment: Shopify revenue includes 25% MVA, costs are excl. MVA
    items_df["revenue_excl_mva"] = items_df["revenue"] / 1.25
    items_df["mva_collected"] = items_df["revenue"] - items_df["revenue_excl_mva"]

    # Join on product_key
    merged = items_df.merge(costs_df[["product_key", "cost_price"]], on="product_key", how="left")
    merged["total_cost"] = merged["cost_price"] * merged["quantity"]
    merged["profit"] = merged["revenue_excl_mva"] - merged["total_cost"]
    merged["margin_pct"] = (merged["profit"] / merged["revenue_excl_mva"] * 100).round(1)

    return merged, items_df, costs_df, overhead


# --- Load data ---
with st.spinner("Laster bestillinger og kostnadsdata..."):
    merged_df, items_df, costs_df, overhead = load_data()

# --- Calculate totals ---
num_orders = merged_df["order_number"].nunique()
total_revenue_incl_mva = merged_df["revenue"].sum()
total_revenue = merged_df["revenue_excl_mva"].sum()
total_mva = merged_df["mva_collected"].sum()
total_cogs = merged_df["total_cost"].sum()
gross_profit = total_revenue - total_cogs

# Monthly fixed costs - prorate based on date range
date_range = (merged_df["order_date"].max() - merged_df["order_date"].min()).days
months_covered = max(date_range / 30, 1)
total_fixed_overhead = overhead["fixed_monthly_total"] * months_covered

# Net profit after all costs
net_profit = gross_profit - total_fixed_overhead
net_margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0

# --- Header ---
st.title("📊 Lønnsomhetsdashboard")
st.caption(f"{len(merged_df)} linjer fordelt på {num_orders} bestillinger")

# --- KPI cards ---
col1, col2, col3, col4, col5, col6 = st.columns(6)

col1.metric("Omsetning (inkl. MVA)", f"{total_revenue_incl_mva:,.0f} kr")
col2.metric("MVA (25%)", f"{total_mva:,.0f} kr")
col3.metric("Omsetning (eksl. MVA)", f"{total_revenue:,.0f} kr")
col4.metric("Varekostnad", f"{total_cogs:,.0f} kr")
col5.metric("Netto resultat", f"{net_profit:,.0f} kr")
col6.metric("Netto margin", f"{net_margin:.1f}%")

# --- Cost breakdown ---
st.divider()
st.subheader("Kostnadsfordeling")

breakdown_col1, breakdown_col2 = st.columns(2)

with breakdown_col1:
    st.markdown("**Faste månedlige kostnader**")
    fixed_data = [{"Kostnad": k, "Beløp (NOK)": v} for k, v in overhead["fixed_monthly"].items()]
    fixed_data.append({"Kostnad": "Totalt (månedlig)", "Beløp (NOK)": overhead["fixed_monthly_total"]})
    st.dataframe(
        pd.DataFrame(fixed_data).style.format({"Beløp (NOK)": "{:,.2f}"}),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(f"Periode: {months_covered:.1f} måneder → {total_fixed_overhead:,.0f} kr totalt i faste kostnader")

with breakdown_col2:
    st.markdown("**Sammendrag**")
    summary_data = [
        {"Post": "Omsetning (inkl. MVA)", "Beløp (NOK)": total_revenue_incl_mva},
        {"Post": "MVA til staten (25%)", "Beløp (NOK)": -total_mva},
        {"Post": "Omsetning (eksl. MVA)", "Beløp (NOK)": total_revenue},
        {"Post": "Varekostnad (COGS)", "Beløp (NOK)": -total_cogs},
        {"Post": "Bruttofortjeneste", "Beløp (NOK)": gross_profit},
        {"Post": f"Faste kostnader ({months_covered:.1f} mnd)", "Beløp (NOK)": -total_fixed_overhead},
        {"Post": "Netto resultat", "Beløp (NOK)": net_profit},
    ]
    st.dataframe(
        pd.DataFrame(summary_data).style.format({"Beløp (NOK)": "{:,.0f}"}),
        use_container_width=True,
        hide_index=True,
    )

# --- Warning for unmatched products ---
unmatched = merged_df[merged_df["cost_price"].isna()]
if len(unmatched) > 0:
    with st.expander(f"⚠️ {unmatched['product_key'].nunique()} produkter uten kostnadsdata ({len(unmatched)} linjer)"):
        st.dataframe(
            unmatched[["product_key", "quantity", "revenue_excl_mva"]]
            .rename(columns={"product_key": "Produkt", "quantity": "Antall", "revenue_excl_mva": "Omsetning (eksl. MVA)"})
            .groupby("Produkt")
            .agg({"Antall": "sum", "Omsetning (eksl. MVA)": "sum"})
            .sort_values("Omsetning (eksl. MVA)", ascending=False)
            .reset_index(),
            use_container_width=True,
        )

st.divider()

# --- Charts ---
chart_col1, chart_col2 = st.columns(2)

# Profit over time (weekly)
with chart_col1:
    st.subheader("Fortjeneste per uke")
    weekly = merged_df.copy()
    weekly["week"] = weekly["order_date"].dt.to_period("W").apply(lambda r: r.start_time)
    weekly = (
        weekly.groupby("week")
        .agg({"revenue_excl_mva": "sum", "total_cost": "sum", "profit": "sum"})
        .reset_index()
    )
    fig = px.line(
        weekly,
        x="week",
        y=["revenue_excl_mva", "total_cost", "profit"],
        labels={"value": "NOK", "week": "Uke", "variable": ""},
        color_discrete_map={"revenue_excl_mva": "#2ecc71", "total_cost": "#e74c3c", "profit": "#3498db"},
    )
    fig.update_layout(hovermode="x unified", legend=dict(orientation="h", y=-0.2))
    fig.for_each_trace(lambda t: t.update(name={"revenue_excl_mva": "Omsetning", "total_cost": "Varekostnad", "profit": "Fortjeneste"}[t.name]))
    st.plotly_chart(fig, use_container_width=True)

# Profit by product (top 15)
with chart_col2:
    st.subheader("Fortjeneste per produkt (Topp 15)")
    product_profit = (
        merged_df.groupby("product_title")
        .agg({"profit": "sum", "revenue_excl_mva": "sum", "quantity": "sum"})
        .sort_values("profit", ascending=False)
        .head(15)
        .reset_index()
    )
    fig2 = px.bar(
        product_profit,
        x="profit",
        y="product_title",
        orientation="h",
        labels={"profit": "Fortjeneste (NOK)", "product_title": ""},
        color="profit",
        color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
    )
    fig2.update_layout(showlegend=False, coloraxis_showscale=False, yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig2, use_container_width=True)

st.divider()

# --- Margin by product ---
st.subheader("Margin % per produkt")
margin_df = (
    merged_df.dropna(subset=["cost_price"])
    .groupby("product_title")
    .agg({"revenue_excl_mva": "sum", "total_cost": "sum", "profit": "sum", "quantity": "sum"})
    .reset_index()
)
margin_df["margin_pct"] = (margin_df["profit"] / margin_df["revenue_excl_mva"] * 100).round(1)
margin_df = margin_df.sort_values("margin_pct", ascending=False)

fig3 = px.bar(
    margin_df,
    x="product_title",
    y="margin_pct",
    labels={"margin_pct": "Margin %", "product_title": ""},
    color="margin_pct",
    color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
)
fig3.update_layout(xaxis_tickangle=-45, showlegend=False, coloraxis_showscale=False)
st.plotly_chart(fig3, use_container_width=True)

st.divider()

# --- Orders table ---
st.subheader("Alle bestillinger")
orders_summary = (
    merged_df.groupby(["order_number", "order_date", "financial_status"])
    .agg({"revenue_excl_mva": "sum", "total_cost": "sum", "profit": "sum"})
    .reset_index()
    .sort_values("order_date", ascending=False)
    .rename(columns={
        "order_number": "Bestilling",
        "order_date": "Dato",
        "financial_status": "Status",
        "revenue_excl_mva": "Omsetning",
        "total_cost": "Varekostnad",
        "profit": "Fortjeneste",
    })
)
orders_summary["Margin %"] = (orders_summary["Fortjeneste"] / orders_summary["Omsetning"] * 100).round(1)
st.dataframe(
    orders_summary.style.format(
        {"Omsetning": "{:,.0f}", "Varekostnad": "{:,.0f}", "Fortjeneste": "{:,.0f}", "Margin %": "{:.1f}%"}
    ),
    use_container_width=True,
    height=400,
)

# --- Detailed line items (expandable) ---
with st.expander("📋 Detaljerte linjer"):
    display_df = merged_df[[
        "order_number", "order_date", "product_key", "quantity",
        "unit_price", "revenue_excl_mva", "cost_price", "total_cost", "profit", "margin_pct",
    ]].rename(columns={
        "order_number": "Bestilling",
        "order_date": "Dato",
        "product_key": "Produkt",
        "quantity": "Antall",
        "unit_price": "Enhetspris",
        "revenue_excl_mva": "Omsetning (eksl. MVA)",
        "cost_price": "Innkjøpspris",
        "total_cost": "Varekostnad",
        "profit": "Fortjeneste",
        "margin_pct": "Margin %",
    }).sort_values("Dato", ascending=False)
    st.dataframe(display_df, use_container_width=True, height=500)

# --- Refresh button ---
st.sidebar.title("⚙️ Innstillinger")
if st.sidebar.button("🔄 Oppdater data"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.caption("Data oppdateres automatisk hver time.")
st.sidebar.caption(f"Butikk: {SHOP}.myshopify.com")
