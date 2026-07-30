import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from shopify_client import get_access_token, fetch_all_orders, extract_line_items
from sheets_client import get_cost_data

st.set_page_config(page_title="Profit Dashboard", page_icon="📊", layout="wide")

# --- Authentication gate ---
if not st.user.is_logged_in:
    st.title("🔐 Profit Dashboard")
    st.write("Log in to view your store analytics.")
    if st.button("Log in with Google"):
        st.login()
    st.stop()


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

    # Join on product_key
    merged = items_df.merge(costs_df[["product_key", "cost_price"]], on="product_key", how="left")
    merged["total_cost"] = merged["cost_price"] * merged["quantity"]
    merged["profit"] = merged["revenue"] - merged["total_cost"]
    merged["margin_pct"] = (merged["profit"] / merged["revenue"] * 100).round(1)

    return merged, items_df, costs_df


# --- Load data ---
with st.spinner("Loading orders and cost data..."):
    merged_df, items_df, costs_df = load_data()

# --- Header ---
st.title("📊 Profit Dashboard")
st.caption(f"Logged in as {st.user.name} • {len(merged_df)} line items across {merged_df['order_number'].nunique()} orders")

# --- KPI cards ---
col1, col2, col3, col4 = st.columns(4)

total_revenue = merged_df["revenue"].sum()
total_cost = merged_df["total_cost"].sum()
total_profit = merged_df["profit"].sum()
avg_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0

col1.metric("Revenue", f"{total_revenue:,.0f} NOK")
col2.metric("Cost (COGS)", f"{total_cost:,.0f} NOK")
col3.metric("Profit", f"{total_profit:,.0f} NOK")
col4.metric("Margin", f"{avg_margin:.1f}%")

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
