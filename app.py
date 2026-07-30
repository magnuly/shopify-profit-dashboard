import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from shopify_client import get_access_token, fetch_all_orders, extract_line_items
from sheets_client import get_cost_data, get_overhead_costs

st.set_page_config(page_title="Profit Dashboard", page_icon="📊", layout="wide")

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

    # Join on product_key
    merged = items_df.merge(costs_df[["product_key", "cost_price"]], on="product_key", how="left")
    merged["total_cost"] = merged["cost_price"] * merged["quantity"]
    merged["profit"] = merged["revenue"] - merged["total_cost"]
    merged["margin_pct"] = (merged["profit"] / merged["revenue"] * 100).round(1)

    return merged, items_df, costs_df, overhead


# --- Load data ---
with st.spinner("Loading orders and cost data..."):
    merged_df, items_df, costs_df, overhead = load_data()

# --- Calculate totals ---
num_orders = merged_df["order_number"].nunique()
total_revenue = merged_df["revenue"].sum()
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
st.title("📊 Profit Dashboard")
st.caption(f"{len(merged_df)} line items across {num_orders} orders")

# --- KPI cards ---
col1, col2, col3, col4, col5 = st.columns(5)

col1.metric("Revenue", f"{total_revenue:,.0f} NOK")
col2.metric("COGS", f"{total_cogs:,.0f} NOK")
col3.metric("Gross Profit", f"{gross_profit:,.0f} NOK")
col4.metric("Net Profit", f"{net_profit:,.0f} NOK")
col5.metric("Net Margin", f"{net_margin:.1f}%")

# --- Cost breakdown ---
st.divider()
st.subheader("Cost Breakdown")

breakdown_col1, breakdown_col2 = st.columns(2)

with breakdown_col1:
    st.markdown("**Fixed Monthly Costs**")
    fixed_data = [{"Cost": k, "Amount (NOK)": v} for k, v in overhead["fixed_monthly"].items()]
    fixed_data.append({"Cost": "Total (monthly)", "Amount (NOK)": overhead["fixed_monthly_total"]})
    st.dataframe(
        pd.DataFrame(fixed_data).style.format({"Amount (NOK)": "{:,.2f}"}),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(f"Period covered: {months_covered:.1f} months → {total_fixed_overhead:,.0f} NOK total overhead")

with breakdown_col2:
    st.markdown("**Summary**")
    summary_data = [
        {"Item": "Total Revenue", "Amount (NOK)": total_revenue},
        {"Item": "Product Costs (COGS)", "Amount (NOK)": -total_cogs},
        {"Item": "Gross Profit", "Amount (NOK)": gross_profit},
        {"Item": f"Fixed Overhead ({months_covered:.1f} months)", "Amount (NOK)": -total_fixed_overhead},
        {"Item": "Net Profit", "Amount (NOK)": net_profit},
    ]
    st.dataframe(
        pd.DataFrame(summary_data).style.format({"Amount (NOK)": "{:,.0f}"}),
        use_container_width=True,
        hide_index=True,
    )

# --- Profit waterfall ---
st.divider()
st.subheader("Profit Waterfall")

waterfall_fig = go.Figure(go.Waterfall(
    x=["Revenue", "COGS", "Gross Profit", "Fixed Overhead", "Net Profit"],
    y=[total_revenue, -total_cogs, 0, -total_fixed_overhead, 0],
    measure=["absolute", "relative", "total", "relative", "total"],
    connector={"line": {"color": "rgb(63, 63, 63)"}},
    increasing={"marker": {"color": "#2ecc71"}},
    decreasing={"marker": {"color": "#e74c3c"}},
    totals={"marker": {"color": "#3498db"}},
))
waterfall_fig.update_layout(showlegend=False)
st.plotly_chart(waterfall_fig, use_container_width=True)

# --- Warning for unmatched products ---
unmatched = merged_df[merged_df["cost_price"].isna()]
if len(unmatched) > 0:
    with st.expander(f"⚠️ {unmatched['product_key'].nunique()} products without cost data ({len(unmatched)} line items)"):
        st.dataframe(
            unmatched[["product_key", "quantity", "revenue"]]
            .groupby("product_key")
            .agg({"quantity": "sum", "revenue": "sum"})
            .sort_values("revenue", ascending=False)
            .reset_index(),
            use_container_width=True,
        )

st.divider()

# --- Charts ---
chart_col1, chart_col2 = st.columns(2)

# Profit over time
with chart_col1:
    st.subheader("Profit Over Time")
    daily = (
        merged_df.groupby("order_date")
        .agg({"revenue": "sum", "total_cost": "sum", "profit": "sum"})
        .reset_index()
    )
    fig = px.line(
        daily,
        x="order_date",
        y=["revenue", "total_cost", "profit"],
        labels={"value": "NOK", "order_date": "Date", "variable": ""},
        color_discrete_map={"revenue": "#2ecc71", "total_cost": "#e74c3c", "profit": "#3498db"},
    )
    fig.update_layout(hovermode="x unified", legend=dict(orientation="h", y=-0.2))
    st.plotly_chart(fig, use_container_width=True)

# Profit by product (top 15)
with chart_col2:
    st.subheader("Profit by Product (Top 15)")
    product_profit = (
        merged_df.groupby("product_title")
        .agg({"profit": "sum", "revenue": "sum", "quantity": "sum"})
        .sort_values("profit", ascending=False)
        .head(15)
        .reset_index()
    )
    fig2 = px.bar(
        product_profit,
        x="profit",
        y="product_title",
        orientation="h",
        labels={"profit": "Profit (NOK)", "product_title": ""},
        color="profit",
        color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
    )
    fig2.update_layout(showlegend=False, coloraxis_showscale=False, yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig2, use_container_width=True)

st.divider()

# --- Margin by product ---
st.subheader("Margin % by Product")
margin_df = (
    merged_df.dropna(subset=["cost_price"])
    .groupby("product_title")
    .agg({"revenue": "sum", "total_cost": "sum", "profit": "sum", "quantity": "sum"})
    .reset_index()
)
margin_df["margin_pct"] = (margin_df["profit"] / margin_df["revenue"] * 100).round(1)
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
st.subheader("All Orders")
orders_summary = (
    merged_df.groupby(["order_number", "order_date", "financial_status"])
    .agg({"revenue": "sum", "total_cost": "sum", "profit": "sum"})
    .reset_index()
    .sort_values("order_date", ascending=False)
)
orders_summary["margin_pct"] = (orders_summary["profit"] / orders_summary["revenue"] * 100).round(1)
st.dataframe(
    orders_summary.style.format(
        {"revenue": "{:,.0f}", "total_cost": "{:,.0f}", "profit": "{:,.0f}", "margin_pct": "{:.1f}%"}
    ),
    use_container_width=True,
    height=400,
)

# --- Detailed line items (expandable) ---
with st.expander("📋 Detailed Line Items"):
    display_cols = [
        "order_number", "order_date", "product_key", "quantity",
        "unit_price", "revenue", "cost_price", "total_cost", "profit", "margin_pct",
    ]
    st.dataframe(
        merged_df[display_cols].sort_values("order_date", ascending=False),
        use_container_width=True,
        height=500,
    )

# --- Refresh button ---
st.sidebar.title("⚙️ Settings")
if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

st.sidebar.caption("Data refreshes automatically every hour.")
st.sidebar.caption(f"Store: {SHOP}.myshopify.com")
