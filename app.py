import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from shopify_client import get_access_token, fetch_all_orders, fetch_transaction_fees, extract_line_items
from sheets_client import get_cost_data, get_overhead_costs, get_per_order_costs
from vipps_client import get_vipps_access_token, get_vipps_ledger_id, fetch_vipps_fees

st.set_page_config(page_title="Lønnsomhetsdashboard", page_icon="📊", layout="wide")

# --- Sidebar (always visible) ---
st.sidebar.title("⚙️ Innstillinger")
if st.sidebar.button("🔄 Oppdater data"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption("Data oppdateres automatisk hver time.")

# --- Product profit calculator ---
st.sidebar.divider()
st.sidebar.title("🧮 Produktkalkulator")
st.sidebar.caption("Beregn fortjeneste for et nytt produkt")

calc_cost = st.sidebar.number_input("Innkjøpspris (eksl. MVA)", min_value=0.0, value=0.0, step=1.0, format="%.2f", key="calc_cost")
calc_sell = st.sidebar.number_input("Salgspris (inkl. MVA)", min_value=0.0, value=0.0, step=1.0, format="%.2f", key="calc_sell")

if calc_sell > 0:
    calc_sell_excl_mva = calc_sell / 1.25
    calc_mva = calc_sell - calc_sell_excl_mva
    calc_gross_profit = calc_sell_excl_mva - calc_cost
    calc_margin = (calc_gross_profit / calc_sell_excl_mva * 100) if calc_sell_excl_mva > 0 else 0

    # Estimate transaction fee (average of Vipps 2% and Card 5%+2kr)
    calc_txn_fee_vipps = calc_sell_excl_mva * 0.02
    calc_txn_fee_kort = calc_sell_excl_mva * 0.05 + 2

    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Salgspris eksl. MVA:** {calc_sell_excl_mva:,.2f} kr")
    st.sidebar.markdown(f"**MVA (25%):** {calc_mva:,.2f} kr")
    st.sidebar.markdown(f"**Innkjøpspris:** {calc_cost:,.2f} kr")
    st.sidebar.markdown(f"**Bruttofortjeneste:** {calc_gross_profit:,.2f} kr")
    st.sidebar.markdown(f"**Margin:** {calc_margin:.1f}%")
    st.sidebar.markdown("---")
    st.sidebar.caption("Estimerte transaksjonsgebyrer:")
    st.sidebar.markdown(f"Vipps (2%): {calc_txn_fee_vipps:,.2f} kr → netto **{calc_gross_profit - calc_txn_fee_vipps:,.2f} kr**")
    st.sidebar.markdown(f"Kort (5%+2kr): {calc_txn_fee_kort:,.2f} kr → netto **{calc_gross_profit - calc_txn_fee_kort:,.2f} kr")

# --- Load secrets ---
shopify_cfg = st.secrets["shopify"]
google_cfg = st.secrets["google"]
vipps_cfg = st.secrets["vipps"]
SHOP = shopify_cfg["shop"]
CLIENT_ID = shopify_cfg["client_id"]
CLIENT_SECRET = shopify_cfg["client_secret"]
SPREADSHEET_ID = google_cfg["spreadsheet_id"]
SERVICE_ACCOUNT_INFO = dict(google_cfg["service_account"])
VIPPS_CLIENT_ID = vipps_cfg["client_id"]
VIPPS_CLIENT_SECRET = vipps_cfg["client_secret"]
VIPPS_SUBSCRIPTION_KEY = vipps_cfg["subscription_key"]
VIPPS_MSN = vipps_cfg["msn"]

# --- Sidebar links (after secrets loaded) ---
st.sidebar.divider()
st.sidebar.markdown("**Hurtiglenker**")
st.sidebar.markdown(f"[🛒 Shopify Admin](https://admin.shopify.com/store/{SHOP})")
st.sidebar.markdown("[🌐 nariz.no](https://nariz.no)")
st.sidebar.markdown(f"[📊 Kostnadsark](https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID})")


# --- Data loading (cached) ---
@st.cache_data(ttl=3600, show_spinner=False)
def load_data(_version="v13"):
    # Shopify orders
    token = get_access_token(CLIENT_ID, CLIENT_SECRET, SHOP)
    orders = fetch_all_orders(token, SHOP)
    items_df = pd.DataFrame(extract_line_items(orders))
    items_df["order_date"] = pd.to_datetime(items_df["order_date"])

    # Actual transaction fees from Shopify Payments
    fee_map = fetch_transaction_fees(token, SHOP)

    # Actual transaction fees from Vipps
    try:
        vipps_token = get_vipps_access_token(VIPPS_CLIENT_ID, VIPPS_CLIENT_SECRET, VIPPS_SUBSCRIPTION_KEY, VIPPS_MSN)
        vipps_ledger_id = get_vipps_ledger_id(vipps_token, VIPPS_SUBSCRIPTION_KEY, VIPPS_MSN)
        vipps_fees_data = fetch_vipps_fees(vipps_token, VIPPS_SUBSCRIPTION_KEY, VIPPS_MSN, vipps_ledger_id)
    except Exception:
        vipps_fees_data = {"total_fees": 0, "total_captured": 0, "avg_rate": 0, "num_transactions": 0}

    # Revenue = (item price * qty) - discounts + shipping revenue
    items_df["item_revenue"] = items_df["unit_price"] * items_df["quantity"] - items_df["total_discount"]
    items_df["revenue"] = items_df["item_revenue"] + items_df["shipping_revenue"]

    # Cost data from Google Sheets
    costs_df = get_cost_data(SERVICE_ACCOUNT_INFO, SPREADSHEET_ID)

    # Overhead costs
    overhead = get_overhead_costs(SERVICE_ACCOUNT_INFO, SPREADSHEET_ID)

    # Per-order costs
    per_order = get_per_order_costs(SERVICE_ACCOUNT_INFO, SPREADSHEET_ID)

    # MVA (VAT) adjustment: Shopify revenue includes 25% MVA, costs are excl. MVA
    items_df["revenue_excl_mva"] = items_df["revenue"] / 1.25
    items_df["mva_collected"] = items_df["revenue"] - items_df["revenue_excl_mva"]
    items_df["shipping_revenue_excl_mva"] = items_df["shipping_revenue"] / 1.25

    # Join on product_key
    merged = items_df.merge(costs_df[["product_key", "cost_price"]], on="product_key", how="left")
    merged["total_cost"] = merged["cost_price"] * merged["quantity"]
    merged["profit"] = merged["revenue_excl_mva"] - merged["total_cost"]
    merged["margin_pct"] = (merged["profit"] / merged["revenue_excl_mva"] * 100).round(1)

    # Exclude refunded orders from profit calculations
    merged["is_refunded"] = merged["financial_status"].isin(["refunded"])

    return merged, items_df, costs_df, overhead, per_order, fee_map, vipps_fees_data


# --- Load data ---
with st.spinner("Laster bestillinger og kostnadsdata..."):
    merged_df, items_df, costs_df, overhead, per_order, fee_map, vipps_fees_data = load_data()

# --- Calculate totals (excluding refunded orders) ---
active_df = merged_df[~merged_df["is_refunded"]]
num_orders = active_df["order_number"].nunique()
num_refunded = merged_df[merged_df["is_refunded"]]["order_number"].nunique()
total_revenue_incl_mva = active_df["revenue"].sum()
total_revenue = active_df["revenue_excl_mva"].sum()
total_mva = active_df["mva_collected"].sum()
total_cogs = active_df["total_cost"].sum()
total_discounts = active_df["total_discount"].sum()
total_shipping_revenue = active_df["shipping_revenue"].sum()
gross_profit = total_revenue - total_cogs

# Per-order costs: fixed costs per order + actual transaction fees
txn_fees = per_order["transaction_fees"]
fixed_per_order_total = per_order["fixed_per_order_total"]

# Use actual fees from Shopify Payments API for card orders
# Use actual fees from Vipps Report API for Vipps orders
# Aggregate total revenue per order (sum of all line items)
order_level = (
    active_df.groupby("order_number")
    .agg(
        order_id=("order_id", "first"),
        payment_method=("payment_method", "first"),
        revenue=("revenue", "sum"),
    )
    .reset_index()
)
order_level["revenue_excl_mva"] = order_level["revenue"] / 1.25

# Map actual Shopify Payments fees by order_id
order_level["actual_fee"] = order_level["order_id"].map(fee_map)

# For Vipps orders: use actual Vipps fee rate from Report API
vipps_actual_rate = vipps_fees_data["avg_rate"] if vipps_fees_data["avg_rate"] > 0 else 0.02

def calc_txn_fee(row):
    if pd.notna(row["actual_fee"]):
        return row["actual_fee"]
    # Use actual Vipps rate from Report API
    method = row["payment_method"].lower()
    rev = row["revenue_excl_mva"]
    if "vipps" in method:
        return rev * vipps_actual_rate
    else:
        # Fallback for unknown methods
        fee_info = txn_fees.get("kort", {"rate": 0, "fixed": 0})
        return rev * fee_info["rate"] + fee_info["fixed"]

order_level["txn_fee"] = order_level.apply(calc_txn_fee, axis=1)
total_txn_fees = order_level["txn_fee"].sum()
orders_with_actual_fees = order_level["actual_fee"].notna().sum()
total_per_order_fixed = fixed_per_order_total * num_orders
total_per_order_costs = total_txn_fees + total_per_order_fixed

# Monthly fixed costs - prorate based on date range
date_range = (active_df["order_date"].max() - active_df["order_date"].min()).days
months_covered = max(date_range / 30, 1)
total_fixed_overhead = overhead["fixed_monthly_total"] * months_covered

# Net profit after all costs
net_profit = gross_profit - total_per_order_costs - total_fixed_overhead
net_margin = (net_profit / total_revenue * 100) if total_revenue > 0 else 0

# --- Header ---
st.title("📊 Lønnsomhetsdashboard")
st.caption(f"{len(active_df)} linjer fordelt på {num_orders} bestillinger" + (f" ({num_refunded} refundert ekskludert)" if num_refunded > 0 else ""))

# --- KPI cards with tooltips ---
col1, col2, col3, col4, col5, col6 = st.columns(6)

col1.metric("Omsetning (inkl. MVA)", f"{total_revenue_incl_mva:,.0f} kr", help="Total omsetning inkludert 25% merverdiavgift. Dette er hva kundene faktisk betalte.")
col2.metric("MVA (25%)", f"{total_mva:,.0f} kr", help="Merverdiavgift som skal betales til staten. 25% av omsetningen ekskl. MVA.")
col3.metric("Omsetning (eksl. MVA)", f"{total_revenue:,.0f} kr", help="Omsetning etter at MVA er trukket fra. Dette er det du faktisk sitter igjen med før kostnader.")
col4.metric("Varekostnad", f"{total_cogs:,.0f} kr", help="Innkjøpskostnad for varene (COGS). Hentet fra Google Sheets-arket 'Produktpriser'.")
col5.metric("Netto resultat", f"{net_profit:,.0f} kr", help="Endelig resultat etter alle kostnader: varekostnad, transaksjonsgebyrer, ordrekostnader, og faste månedlige utgifter.")
col6.metric("Netto margin", f"{net_margin:.1f}%", help="Netto resultat som prosent av omsetning (eksl. MVA). Viser hvor mye du tjener per krone omsatt etter alle kostnader.")

# --- Tabs ---
tab_okonomi, tab_trender, tab_produkter, tab_kunder, tab_rabatter, tab_frakt, tab_breakeven, tab_bestillinger, tab_om = st.tabs([
    "💰 Økonomi",
    "📈 Trender",
    "🛍️ Produkter",
    "👥 Kunder",
    "🏷️ Rabatter",
    "📦 Frakt",
    "📊 Break-even",
    "📋 Bestillinger",
    "ℹ️ Om",
])

with tab_okonomi:
    st.subheader("Kostnadsfordeling", help="Oversikt over alle kostnader fordelt på faste månedlige, per bestilling, og transaksjonsgebyrer.")

    breakdown_col1, breakdown_col2, breakdown_col3 = st.columns(3)

    with breakdown_col1:
        st.markdown("**Faste månedlige kostnader**")
        fixed_data = [{"Kostnad": k, "Beløp (NOK)": v} for k, v in overhead["fixed_monthly"].items()]
        fixed_data.append({"Kostnad": "Totalt (månedlig)", "Beløp (NOK)": overhead["fixed_monthly_total"]})
        st.dataframe(
            pd.DataFrame(fixed_data).style.format({"Beløp (NOK)": "{:,.2f}"}),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"Periode: {months_covered:.1f} måneder → {total_fixed_overhead:,.0f} kr totalt")

    with breakdown_col2:
        st.markdown("**Kostnader pr. bestilling**")
        per_order_data = [{"Kostnad": k, "Beløp (NOK)": v} for k, v in per_order["fixed_per_order"].items()]
        per_order_data.append({"Kostnad": "Sum pr. bestilling", "Beløp (NOK)": fixed_per_order_total})
        st.dataframe(
            pd.DataFrame(per_order_data).style.format({"Beløp (NOK)": "{:,.2f}"}),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"{num_orders} bestillinger → {total_per_order_fixed:,.0f} kr totalt")
        st.markdown("**Transaksjonsgebyrer**")
        avg_fee_rate = (total_txn_fees / total_revenue * 100) if total_revenue > 0 else 0
        kort_fees = order_level[order_level['actual_fee'].notna()]['txn_fee'].sum()
        vipps_fees = order_level[order_level['actual_fee'].isna()]['txn_fee'].sum()
        kort_rev = order_level[order_level['actual_fee'].notna()]['revenue_excl_mva'].sum()
        vipps_rev = order_level[order_level['actual_fee'].isna()]['revenue_excl_mva'].sum()
        kort_rate = (kort_fees / kort_rev * 100) if kort_rev > 0 else 0
        vipps_rate = vipps_actual_rate * 100
        txn_data = [
            {"Metode": "Kort (Shopify Payments)", "Kilde": "Faktisk fra API", "Snitt sats": f"{kort_rate:.2f}%", "Totalt gebyr": f"{kort_fees:,.0f} kr"},
            {"Metode": "Vipps", "Kilde": "Faktisk fra API", "Snitt sats": f"{vipps_rate:.2f}%", "Totalt gebyr": f"{vipps_fees:,.0f} kr"},
        ]
        st.dataframe(pd.DataFrame(txn_data), use_container_width=True, hide_index=True)
        st.caption(f"Totalt gebyrer: {total_txn_fees:,.0f} kr (snitt {avg_fee_rate:.2f}% av omsetning)")

    with breakdown_col3:
        st.markdown("**Sammendrag**")
        items_gross = (active_df["unit_price"] * active_df["quantity"]).sum()
        summary_data = [
            {"Post": "Brutto varesalg (inkl. MVA)", "Beløp (NOK)": items_gross},
            {"Post": "Rabatter gitt", "Beløp (NOK)": -total_discounts},
            {"Post": "Fraktinntekter", "Beløp (NOK)": total_shipping_revenue},
            {"Post": "Total omsetning (inkl. MVA)", "Beløp (NOK)": total_revenue_incl_mva},
            {"Post": "MVA til staten (25%)", "Beløp (NOK)": -total_mva},
            {"Post": "Omsetning (eksl. MVA)", "Beløp (NOK)": total_revenue},
            {"Post": "Varekostnad (COGS)", "Beløp (NOK)": -total_cogs},
            {"Post": "Bruttofortjeneste", "Beløp (NOK)": gross_profit},
            {"Post": "Transaksjonsgebyrer", "Beløp (NOK)": -total_txn_fees},
            {"Post": f"Ordrekostnader ({num_orders} stk)", "Beløp (NOK)": -total_per_order_fixed},
            {"Post": f"Faste kostnader ({months_covered:.1f} mnd)", "Beløp (NOK)": -total_fixed_overhead},
            {"Post": "Netto resultat", "Beløp (NOK)": net_profit},
        ]
        st.dataframe(
            pd.DataFrame(summary_data).style.format({"Beløp (NOK)": "{:,.0f}"}),
            use_container_width=True,
            hide_index=True,
        )

    unmatched = active_df[active_df["cost_price"].isna()]
    if len(unmatched) > 0:
        st.markdown(
            f"<h3 style='color:#e74c3c;'>⚠️ {unmatched['product_key'].nunique()} produkter uten kostnadsdata ({len(unmatched)} linjer)</h3>",
            unsafe_allow_html=True,
        )
        st.warning("Disse produktene mangler innkjøpspris og kan derfor gi feil fortjeneste og margin.")
        st.dataframe(
            unmatched[["product_key", "quantity", "revenue_excl_mva"]]
            .rename(columns={"product_key": "Produkt", "quantity": "Antall", "revenue_excl_mva": "Omsetning (eksl. MVA)"})
            .groupby("Produkt")
            .agg({"Antall": "sum", "Omsetning (eksl. MVA)": "sum"})
            .sort_values("Omsetning (eksl. MVA)", ascending=False)
            .reset_index(),
            use_container_width=True,
        )

    # --- Projected year result ---
    st.divider()
    st.subheader("Prognose for regnskapsåret", help="Projisert årsresultat basert på historisk daglig gjennomsnitt, ekstrapolert til hele regnskapsåret (1. jan – 31. des).")

    from datetime import date as date_type
    today = date_type.today()
    year_start = date_type(today.year, 1, 1)
    year_end = date_type(today.year, 12, 31)
    days_in_year = (year_end - year_start).days + 1

    # Days with data in this calendar year
    year_orders = active_df[active_df["order_date"].dt.year == today.year]
    if len(year_orders) > 0:
        first_order_date = year_orders["order_date"].min().date()
        last_order_date = year_orders["order_date"].max().date()
        days_with_data = (last_order_date - first_order_date).days + 1
        days_remaining = (year_end - today).days

        # Daily averages from actual data
        daily_revenue = year_orders["revenue_excl_mva"].sum() / days_with_data
        daily_cogs = year_orders["total_cost"].sum() / days_with_data
        daily_orders = year_orders["order_number"].nunique() / days_with_data

        # Project to full year
        projected_revenue = daily_revenue * days_in_year
        projected_cogs = daily_cogs * days_in_year
        projected_orders = daily_orders * days_in_year
        projected_txn_fees = projected_revenue * (total_txn_fees / total_revenue) if total_revenue > 0 else 0
        projected_per_order_costs = fixed_per_order_total * projected_orders
        projected_fixed_overhead = overhead["fixed_monthly_total"] * 12
        projected_net_profit = projected_revenue - projected_cogs - projected_txn_fees - projected_per_order_costs - projected_fixed_overhead
        projected_margin = (projected_net_profit / projected_revenue * 100) if projected_revenue > 0 else 0

        # Current actual YTD
        ytd_revenue = year_orders["revenue_excl_mva"].sum()
        ytd_net = net_profit  # Already calculated above

        prog_col1, prog_col2, prog_col3 = st.columns(3)
        prog_col1.metric("Projisert omsetning (år)", f"{projected_revenue:,.0f} kr")
        prog_col2.metric("Projisert netto resultat (år)", f"{projected_net_profit:,.0f} kr")
        prog_col3.metric("Projisert netto margin", f"{projected_margin:.1f}%")

        st.markdown(f"**Basert på:** {days_with_data} dager med data ({first_order_date.strftime('%d.%m')} – {last_order_date.strftime('%d.%m.%Y')})")
        st.markdown(f"**Daglig snitt:** {daily_orders:.1f} bestillinger / {daily_revenue:,.0f} kr omsetning")

        prognose_data = [
            {"Post": "Projisert omsetning (eksl. MVA)", "Beløp (NOK)": projected_revenue},
            {"Post": "Projisert varekostnad", "Beløp (NOK)": -projected_cogs},
            {"Post": "Projisert transaksjonsgebyrer", "Beløp (NOK)": -projected_txn_fees},
            {"Post": f"Projisert ordrekostnader ({projected_orders:.0f} stk)", "Beløp (NOK)": -projected_per_order_costs},
            {"Post": "Faste kostnader (12 mnd)", "Beløp (NOK)": -projected_fixed_overhead},
            {"Post": "Projisert netto resultat", "Beløp (NOK)": projected_net_profit},
        ]
        st.dataframe(
            pd.DataFrame(prognose_data).style.format({"Beløp (NOK)": "{:,.0f}"}),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(f"⚠️ Prognosen antar at resten av året følger samme takt som de siste {days_with_data} dagene.")
    else:
        st.info("Ingen bestillinger funnet for inneværende år.")




with tab_trender:
    chart_col1, chart_col2 = st.columns(2)

    # Profit over time (weekly)
    with chart_col1:
        st.subheader("Fortjeneste per uke", help="Ukentlig oversikt over omsetning (eksl. MVA), varekostnad og fortjeneste. Fortjeneste = Omsetning eksl. MVA − Varekostnad.")
        weekly = active_df.copy()
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
        st.subheader("Fortjeneste per produkt (Topp 15)", help="De 15 produktene med høyest total fortjeneste. Fortjeneste = Omsetning eksl. MVA − Innkjøpspris.")
        product_profit = (
            active_df.groupby("product_title")
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



    st.subheader("Gjennomsnittlig ordreverdi", help="Ukentlig gjennomsnittlig ordreverdi beregnet som omsetning eksl. MVA delt på antall unike bestillinger i samme uke.")
    order_totals = (
        active_df.groupby("order_number")
        .agg(
            order_date=("order_date", "min"),
            revenue_excl_mva=("revenue_excl_mva", "sum"),
            total_cost=("total_cost", "sum"),
            total_discount=("total_discount", "sum"),
            shipping_revenue=("shipping_revenue", "sum"),
            discount_code=("discount_code", "first"),
            customer_email=("customer_email", "first"),
        )
        .reset_index()
    )
    overall_aov = total_revenue / num_orders if num_orders > 0 else 0
    st.metric("Gjennomsnittlig ordreverdi", f"{overall_aov:,.0f} kr", help="Total omsetning eksl. MVA delt på antall bestillinger.")
    weekly_aov = order_totals.copy()
    weekly_aov["week"] = weekly_aov["order_date"].dt.to_period("W").apply(lambda r: r.start_time)
    weekly_aov = (
        weekly_aov.groupby("week")
        .agg(omsetning=("revenue_excl_mva", "sum"), bestillinger=("order_number", "nunique"))
        .reset_index()
    )
    weekly_aov["aov"] = weekly_aov["omsetning"] / weekly_aov["bestillinger"]
    fig_aov = px.line(
        weekly_aov,
        x="week",
        y="aov",
        labels={"week": "Uke", "aov": "Gjennomsnittlig ordreverdi (NOK)"},
        color_discrete_sequence=["#2ecc71"],
    )
    fig_aov.update_layout(hovermode="x unified")
    st.plotly_chart(fig_aov, use_container_width=True)



    st.subheader("Daglig ordretakt", help="Antall unike bestillinger per dag over tid, med gjennomsnittlig daglig ordretakt.")
    daily_orders = (
        order_totals.groupby("order_date")
        .agg(bestillinger=("order_number", "nunique"))
        .reset_index()
        .sort_values("order_date")
    )
    avg_orders_per_day = daily_orders["bestillinger"].mean() if len(daily_orders) > 0 else 0
    st.metric("Gjennomsnittlige bestillinger per dag", f"{avg_orders_per_day:.1f}", help="Gjennomsnittlig antall bestillinger på dager med ordredata.")
    fig_daily = px.line(
        daily_orders,
        x="order_date",
        y="bestillinger",
        labels={"order_date": "Dato", "bestillinger": "Bestillinger"},
        color_discrete_sequence=["#3498db"],
    )
    fig_daily.update_layout(hovermode="x unified")
    st.plotly_chart(fig_daily, use_container_width=True)




with tab_produkter:
    st.subheader("Margin % per produkt", help="Fortjenestemargin per produkt. Margin = (Fortjeneste / Omsetning eksl. MVA) × 100. Høyere er bedre. Kun produkter med kjent innkjøpspris vises.")
    margin_df = (
        active_df.dropna(subset=["cost_price"])
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



    st.subheader("Bestselgere", help="De 15 produktene med høyest solgt antall. Rangeringen er basert på volum, ikke fortjeneste.")
    best_sellers = (
        active_df.groupby("product_title")
        .agg(antall_solgt=("quantity", "sum"), omsetning=("revenue_excl_mva", "sum"))
        .reset_index()
        .sort_values("antall_solgt", ascending=False)
        .head(15)
    )
    fig_best = px.bar(
        best_sellers,
        x="antall_solgt",
        y="product_title",
        orientation="h",
        labels={"antall_solgt": "Antall solgt", "product_title": ""},
        color_discrete_sequence=["#3498db"],
    )
    fig_best.update_layout(yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig_best, use_container_width=True)
    st.dataframe(
        best_sellers.rename(columns={
            "product_title": "Produkt",
            "antall_solgt": "Antall solgt",
            "omsetning": "Omsetning (eksl. MVA)",
        }).style.format({"Omsetning (eksl. MVA)": "{:,.0f}"}),
        use_container_width=True,
        hide_index=True,
    )



    st.subheader("Bidragsmargin", help="Produktbidrag før faste kostnader. Beregnes som omsetning eksl. MVA minus innkjøpspris ganger antall solgt, uten fordeling av overhead.")
    contribution_products = (
        active_df.dropna(subset=["cost_price"])
        .assign(bidragsmargin=lambda df: df["revenue_excl_mva"] - df["cost_price"] * df["quantity"])
        .groupby("product_title")
        .agg(bidragsmargin=("bidragsmargin", "sum"), omsetning=("revenue_excl_mva", "sum"), antall=("quantity", "sum"))
        .reset_index()
        .sort_values("bidragsmargin", ascending=False)
        .head(15)
    )
    fig_contribution = px.bar(
        contribution_products,
        x="bidragsmargin",
        y="product_title",
        orientation="h",
        labels={"bidragsmargin": "Bidragsmargin (NOK)", "product_title": ""},
        color="bidragsmargin",
        color_continuous_scale=["#e74c3c", "#f39c12", "#2ecc71"],
    )
    fig_contribution.update_layout(showlegend=False, coloraxis_showscale=False, yaxis=dict(autorange="reversed"))
    st.plotly_chart(fig_contribution, use_container_width=True)




with tab_kunder:
    st.subheader("Gjentakende kunder", help="Kunder gruppert på e-postadresse for å vise hvor mange som har lagt inn én, to eller tre eller flere bestillinger.")
    customer_orders = order_totals[order_totals["customer_email"].fillna("").str.len() > 0]
    customer_orders = (
        customer_orders.groupby("customer_email")
        .agg(bestillinger=("order_number", "nunique"))
        .reset_index()
    )
    unique_customers = len(customer_orders)
    repeat_customers = len(customer_orders[customer_orders["bestillinger"] >= 2])
    repeat_rate = repeat_customers / unique_customers * 100 if unique_customers > 0 else 0
    rep_col1, rep_col2, rep_col3 = st.columns(3)
    rep_col1.metric("Unike kunder", f"{unique_customers:,}", help="Antall unike kunder med registrert e-postadresse.")
    rep_col2.metric("Kunder med 2+ bestillinger", f"{repeat_customers:,}", help="Antall kunder som har lagt inn minst to bestillinger.")
    rep_col3.metric("Gjenkjøpsrate", f"{repeat_rate:.1f}%", help="Andel kunder med minst to bestillinger.")
    repeat_distribution = pd.DataFrame({
        "Ordrefrekvens": ["1 bestilling", "2 bestillinger", "3+ bestillinger"],
        "Kunder": [
            len(customer_orders[customer_orders["bestillinger"] == 1]),
            len(customer_orders[customer_orders["bestillinger"] == 2]),
            len(customer_orders[customer_orders["bestillinger"] >= 3]),
        ],
    })
    fig_repeat = px.bar(
        repeat_distribution,
        x="Ordrefrekvens",
        y="Kunder",
        labels={"Ordrefrekvens": "Ordrefrekvens", "Kunder": "Antall kunder"},
        color="Ordrefrekvens",
        color_discrete_map={"1 bestilling": "#3498db", "2 bestillinger": "#f39c12", "3+ bestillinger": "#2ecc71"},
    )
    fig_repeat.update_layout(showlegend=False)
    st.plotly_chart(fig_repeat, use_container_width=True)



    st.subheader("Kjøpsmønster", help="Når handler kundene? Fordeling av bestillinger på ukedager og tid på døgnet.")

    # Parse full timestamp for time analysis
    order_times = active_df.drop_duplicates(subset="order_number")[["order_number", "order_created_at"]].copy()
    order_times["timestamp"] = pd.to_datetime(order_times["order_created_at"])
    order_times["weekday"] = order_times["timestamp"].dt.dayofweek
    order_times["weekday_name"] = order_times["timestamp"].dt.day_name()
    order_times["hour"] = order_times["timestamp"].dt.hour

    # Norwegian day names in correct order
    day_map = {0: "Mandag", 1: "Tirsdag", 2: "Onsdag", 3: "Torsdag", 4: "Fredag", 5: "Lørdag", 6: "Søndag"}
    order_times["ukedag"] = order_times["weekday"].map(day_map)

    pattern_col1, pattern_col2 = st.columns(2)

    with pattern_col1:
        st.markdown("**Bestillinger per ukedag**")
        weekday_counts = (
            order_times.groupby(["weekday", "ukedag"])
            .agg(bestillinger=("order_number", "count"))
            .reset_index()
            .sort_values("weekday")
        )
        fig_wd = px.bar(
            weekday_counts,
            x="ukedag",
            y="bestillinger",
            labels={"ukedag": "", "bestillinger": "Antall bestillinger"},
            color="bestillinger",
            color_continuous_scale=["#85c1e9", "#2471a3"],
        )
        fig_wd.update_layout(showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig_wd, use_container_width=True)

    with pattern_col2:
        st.markdown("**Bestillinger per tid på døgnet**")
        hour_counts = (
            order_times.groupby("hour")
            .agg(bestillinger=("order_number", "count"))
            .reset_index()
        )
        # Fill missing hours with 0
        all_hours = pd.DataFrame({"hour": range(24)})
        hour_counts = all_hours.merge(hour_counts, on="hour", how="left").fillna(0)
        hour_counts["bestillinger"] = hour_counts["bestillinger"].astype(int)
        hour_counts["klokkeslett"] = hour_counts["hour"].apply(lambda h: f"{h:02d}:00")

        fig_hr = px.bar(
            hour_counts,
            x="klokkeslett",
            y="bestillinger",
            labels={"klokkeslett": "", "bestillinger": "Antall bestillinger"},
            color="bestillinger",
            color_continuous_scale=["#f9e79f", "#e74c3c"],
        )
        fig_hr.update_layout(showlegend=False, coloraxis_showscale=False)
        st.plotly_chart(fig_hr, use_container_width=True)

    # Peak summary
    peak_day = weekday_counts.loc[weekday_counts["bestillinger"].idxmax(), "ukedag"]
    peak_hour = hour_counts.loc[hour_counts["bestillinger"].idxmax(), "hour"]
    st.caption(f"Mest populær dag: **{peak_day}** • Mest populær tid: **{int(peak_hour):02d}:00–{int(peak_hour)+1:02d}:00**")



    st.subheader("Betalingsmetode og geografi", help="Fordeling av bestillinger etter betalingsmetode (kort/Vipps) og leveringsby.")
    geo_col1, geo_col2 = st.columns(2)

    with geo_col1:
        st.markdown("**Betalingsmetode**")
        # Aggregate at order level to avoid counting line items
        order_payments = (
            active_df.drop_duplicates(subset="order_number")[["order_number", "payment_method", "revenue_excl_mva"]]
        )
        payment_summary = (
            active_df.groupby("payment_method")
            .agg(orders=("order_number", "nunique"), omsetning=("revenue_excl_mva", "sum"))
            .reset_index()
            .sort_values("omsetning", ascending=False)
        )
        # Clean up gateway names for display
        payment_summary["payment_method"] = payment_summary["payment_method"].replace({
            "shopify_payments": "Kort (Shopify Payments)",
            "Vipps/MobilePay Payments": "Vipps",
        })
        fig_pay = px.pie(
            payment_summary,
            values="omsetning",
            names="payment_method",
            hole=0.4,
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig_pay.update_traces(textposition="inside", textinfo="percent+label")
        fig_pay.update_layout(showlegend=False)
        st.plotly_chart(fig_pay, use_container_width=True)
        st.dataframe(
            payment_summary.rename(columns={
                "payment_method": "Metode",
                "orders": "Bestillinger",
                "omsetning": "Omsetning eksl. MVA (NOK)",
            }).style.format({"Omsetning eksl. MVA (NOK)": "{:,.0f}"}),
            use_container_width=True,
            hide_index=True,
        )

    with geo_col2:
        st.markdown("**Bestillinger per by (Topp 15)**")
        city_summary = (
            active_df.groupby("city")
            .agg(orders=("order_number", "nunique"), omsetning=("revenue_excl_mva", "sum"))
            .reset_index()
            .sort_values("orders", ascending=False)
            .head(15)
        )
        fig_city = px.bar(
            city_summary,
            x="orders",
            y="city",
            orientation="h",
            labels={"orders": "Antall bestillinger", "city": ""},
            color="omsetning",
            color_continuous_scale=["#85c1e9", "#2471a3"],
        )
        fig_city.update_layout(coloraxis_showscale=False, yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig_city, use_container_width=True)
        st.dataframe(
            city_summary.rename(columns={
                "city": "By",
                "orders": "Bestillinger",
                "omsetning": "Omsetning (NOK)",
            }).style.format({"Omsetning (NOK)": "{:,.0f}"}),
            use_container_width=True,
            hide_index=True,
        )




with tab_rabatter:
    st.subheader("Rabattkoder", help="Oversikt over hvilke rabattkoder som er brukt, antall bestillinger per kode, og totalt rabattert beløp.")

    discount_summary = (
        active_df.drop_duplicates(subset="order_number")[["order_number", "discount_code", "revenue_excl_mva", "total_discount"]]
        .groupby("discount_code")
        .agg(
            bestillinger=("order_number", "nunique"),
            total_rabatt=("total_discount", "sum"),
        )
        .reset_index()
        .sort_values("bestillinger", ascending=False)
    )
    # Add revenue and profit per discount code (from all line items, not just first per order)
    discount_revenue = (
        active_df.groupby("discount_code")
        .agg(
            omsetning=("revenue_excl_mva", "sum"),
            fortjeneste=("profit", "sum"),
        )
        .reset_index()
    )
    discount_summary = discount_summary.merge(discount_revenue, on="discount_code", how="left")
    discount_summary = discount_summary.rename(columns={"discount_code": "Rabattkode"})

    disc_col1, disc_col2 = st.columns(2)

    with disc_col1:
        fig_disc = px.bar(
            discount_summary,
            x="bestillinger",
            y="Rabattkode",
            orientation="h",
            labels={"bestillinger": "Antall bestillinger", "Rabattkode": ""},
            color="total_rabatt",
            color_continuous_scale=["#f9e79f", "#e74c3c"],
        )
        fig_disc.update_layout(coloraxis_showscale=False, yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig_disc, use_container_width=True)

    with disc_col2:
        st.dataframe(
            discount_summary.rename(columns={
                "bestillinger": "Bestillinger",
                "total_rabatt": "Totalt rabattert (NOK)",
                "omsetning": "Omsetning eksl. MVA (NOK)",
                "fortjeneste": "Fortjeneste (NOK)",
            }).style.format({
                "Totalt rabattert (NOK)": "{:,.0f}",
                "Omsetning eksl. MVA (NOK)": "{:,.0f}",
                "Fortjeneste (NOK)": "{:,.0f}",
            }),
            use_container_width=True,
            hide_index=True,
        )



    st.subheader("Rabattlønnsomhet", help="Viser om rabattkoder gir høyere ordreverdi enn bestillinger uten rabatt, eller hovedsakelig reduserer marginen.")
    without_discount = order_totals[(order_totals["discount_code"] == "Ingen rabatt") | (order_totals["total_discount"] <= 0)]
    avg_without_discount = without_discount["revenue_excl_mva"].mean() if len(without_discount) > 0 else 0
    discount_profitability = (
        order_totals[(order_totals["discount_code"] != "Ingen rabatt") & (order_totals["total_discount"] > 0)]
        .groupby("discount_code")
        .agg(
            bestillinger=("order_number", "nunique"),
            snitt_ordneverdi=("revenue_excl_mva", "mean"),
            total_rabatt=("total_discount", "sum"),
            total_omsetning=("revenue_excl_mva", "sum"),
        )
        .reset_index()
        .sort_values("total_omsetning", ascending=False)
    )
    discount_profitability["Snitt uten rabatt"] = avg_without_discount
    discount_profitability["Vurdering"] = discount_profitability["snitt_ordneverdi"].apply(
        lambda value: "Høyere ordreverdi" if value > avg_without_discount else "Lavere ordreverdi"
    )
    if len(discount_profitability) > 0:
        fig_discount_profit = px.bar(
            discount_profitability,
            x="discount_code",
            y=["snitt_ordneverdi", "Snitt uten rabatt"],
            barmode="group",
            labels={"discount_code": "Rabattkode", "value": "Gjennomsnittlig ordreverdi (NOK)", "variable": ""},
            color_discrete_map={"snitt_ordneverdi": "#f39c12", "Snitt uten rabatt": "#3498db"},
        )
        fig_discount_profit.for_each_trace(lambda t: t.update(name={"snitt_ordneverdi": "Med rabattkode", "Snitt uten rabatt": "Uten rabatt"}[t.name]))
        st.plotly_chart(fig_discount_profit, use_container_width=True)
    else:
        st.info("Ingen rabattkoder med rabattbeløp funnet i aktive bestillinger.")
    st.dataframe(
        discount_profitability.rename(columns={
            "discount_code": "Rabattkode",
            "bestillinger": "Bestillinger",
            "snitt_ordneverdi": "Snitt ordreverdi med rabatt",
            "total_rabatt": "Total rabatt gitt",
            "total_omsetning": "Total omsetning",
        })[["Rabattkode", "Bestillinger", "Snitt ordreverdi med rabatt", "Snitt uten rabatt", "Total rabatt gitt", "Total omsetning", "Vurdering"]].style.format({
            "Snitt ordreverdi med rabatt": "{:,.0f}",
            "Snitt uten rabatt": "{:,.0f}",
            "Total rabatt gitt": "{:,.0f}",
            "Total omsetning": "{:,.0f}",
        }),
        use_container_width=True,
        hide_index=True,
    )




with tab_frakt:
    st.subheader("Fraktlønnsomhet", help="Sammenligner fraktinntekter fra kunder mot estimert fraktkostnad per ordre fra kostnadsarket.")
    estimated_shipping_cost_per_order = per_order["fixed_per_order"].get("Ca fraktkostnad", 0)
    total_estimated_shipping_cost = estimated_shipping_cost_per_order * num_orders
    shipping_profit = total_shipping_revenue - total_estimated_shipping_cost
    ship_col1, ship_col2, ship_col3 = st.columns(3)
    ship_col1.metric("Fraktinntekter", f"{total_shipping_revenue:,.0f} kr", help="Total frakt betalt av kunder.")
    ship_col2.metric("Estimert fraktkostnad", f"{total_estimated_shipping_cost:,.0f} kr", help="Estimert fraktkostnad per ordre multiplisert med antall bestillinger.")
    ship_col3.metric("Fraktresultat", f"{shipping_profit:,.0f} kr", help="Fraktinntekter minus estimert fraktkostnad.")
    weekly_shipping = order_totals.copy()
    weekly_shipping["week"] = weekly_shipping["order_date"].dt.to_period("W").apply(lambda r: r.start_time)
    weekly_shipping = (
        weekly_shipping.groupby("week")
        .agg(fraktinntekter=("shipping_revenue", "sum"), bestillinger=("order_number", "nunique"))
        .reset_index()
    )
    weekly_shipping["estimert_fraktkostnad"] = weekly_shipping["bestillinger"] * estimated_shipping_cost_per_order
    weekly_shipping_chart = weekly_shipping.melt(
        id_vars="week",
        value_vars=["fraktinntekter", "estimert_fraktkostnad"],
        var_name="Type",
        value_name="NOK",
    )
    weekly_shipping_chart["Type"] = weekly_shipping_chart["Type"].replace({
        "fraktinntekter": "Fraktinntekter",
        "estimert_fraktkostnad": "Estimert fraktkostnad",
    })
    fig_ship = px.bar(
        weekly_shipping_chart,
        x="week",
        y="NOK",
        color="Type",
        barmode="group",
        labels={"week": "Uke", "NOK": "NOK"},
        color_discrete_map={"Fraktinntekter": "#2ecc71", "Estimert fraktkostnad": "#e74c3c"},
    )
    st.plotly_chart(fig_ship, use_container_width=True)




with tab_breakeven:
    st.subheader("Break-even analyse", help="Beregner hvor mange bestillinger per måned som trengs for å dekke faste månedlige kostnader basert på gjennomsnittlig bidrag per ordre.")
    fixed_monthly_total = overhead["fixed_monthly_total"]
    avg_contribution_per_order = (total_revenue - total_cogs - total_per_order_costs) / num_orders if num_orders > 0 else 0
    break_even_orders = fixed_monthly_total / avg_contribution_per_order if avg_contribution_per_order > 0 else 0
    current_orders_per_month = num_orders / months_covered if months_covered > 0 else 0
    be_col1, be_col2, be_col3 = st.columns(3)
    be_col1.metric("Break-even per måned", f"{break_even_orders:,.0f} bestillinger", help="Faste månedlige kostnader delt på gjennomsnittlig bidragsmargin per ordre.")
    be_col2.metric("Nåværende ordretakt", f"{current_orders_per_month:,.0f} bestillinger/mnd", help="Antall bestillinger justert til månedlig takt basert på perioden i dataene.")
    be_col3.metric("Bidrag per ordre", f"{avg_contribution_per_order:,.0f} kr", help="Omsetning eksl. MVA minus varekostnad og ordrekostnader, delt på antall bestillinger.")
    break_even_visual = pd.DataFrame({
        "Type": ["Nåværende takt", "Break-even"],
        "Bestillinger per måned": [current_orders_per_month, break_even_orders],
    })
    fig_break_even = px.bar(
        break_even_visual,
        x="Type",
        y="Bestillinger per måned",
        color="Type",
        labels={"Type": "", "Bestillinger per måned": "Bestillinger per måned"},
        color_discrete_map={"Nåværende takt": "#2ecc71", "Break-even": "#e74c3c"},
    )
    fig_break_even.update_layout(showlegend=False)
    st.plotly_chart(fig_break_even, use_container_width=True)




with tab_bestillinger:
    st.subheader("Alle bestillinger", help="Oversikt over alle bestillinger med omsetning, varekostnad og fortjeneste. Refunderte bestillinger er ekskludert.")
    orders_summary = (
        active_df.groupby(["order_number", "order_date", "financial_status"])
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


    with st.expander("📋 Detaljerte linjer"):
        display_df = active_df[[
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



with tab_om:
    st.subheader("Hvordan fungerer dette dashboardet?", help="Teknisk forklaring av dataflyt og kilder.")

    st.markdown("""
```
┌─────────────────────┐         ┌─────────────────────────────────┐
│                     │         │         Google Sheets            │
│   Shopify Admin     │         │                                 │
│   (Bestillinger)    │         │  Tab 1: Produktpriser           │
│                     │         │  Tab 2: Faste avgifter          │
│  • Ordredata        │         │  Tab 3: Avgifter pr. bestilling │
│  • Produkter        │         │                                 │
│  • Rabatter         │         └────────────┬────────────────────┘
│  • Betalingsmetode  │                      │
│  • Fraktinntekter   │                      │
│  • Leveringsadresse │                      │
│  • Kundeinfo        │                      │
└─────────┬───────────┘                      │
          │                                  │
          │  Shopify Admin API               │  Google Sheets API
          │  (henter ny token hver gang)     │  (via tjenestekonto)
          │                                  │
          ▼                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                                                                 │
│                    Streamlit Dashboard                           │
│                                                                 │
│   1. Henter alle bestillinger fra Shopify                       │
│   2. Henter produktpriser fra Google Sheets                     │
│   3. Henter faktiske gebyrer fra Shopify Payments API           │
│   4. Henter faktiske gebyrer fra Vipps Report API               │
│   5. Kobler sammen på produktnavn                               │
│   6. Beregner: Omsetning − MVA − Varekostnad − Gebyrer         │
│      − Ordrekostnader − Faste kostnader = Netto resultat        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

Tilleggs-APIer:
┌─────────────────────────┐    ┌─────────────────────────┐
│  Shopify Payments API   │    │    Vipps Report API     │
│  (Balance Transactions) │    │  (Ledger fees/funds)    │
│                         │    │                         │
│  → Faktiske kortgebyrer │    │  → Faktiske Vipps-      │
│    per transaksjon      │    │    gebyrer per dag      │
│  → Snitt ~2.35%         │    │  → Snitt ~2.66%         │
└─────────────────────────┘    └─────────────────────────┘
```
    """)

    with st.expander("Mer detaljer om beregningene"):
        st.markdown("""
**Datakilder:**
- **Shopify Admin API** — Alle bestillinger med produkter, priser, rabatter, betalingsmetode, leveringsadresse og kundeinfo. Ny tilgangstoken hentes automatisk ved hver lasting (utløper etter 24 timer).
- **Shopify Payments API** — Faktiske transaksjonsgebyrer for kortbetalinger. Hentes fra balance transactions endpoint.
- **Vipps Report API** — Faktiske transaksjonsgebyrer for Vipps-betalinger. Hentes fra ledger fees endpoint per dag.
- **Google Sheets** — Tre faner med kostnadsdata som du vedlikeholder manuelt.

**Beregningsflyt:**

| Steg | Beskrivelse |
|------|-------------|
| 1 | Hent ordredata fra Shopify (alle bestillinger, ekskl. refunderte) |
| 2 | Beregn omsetning per linje: (pris × antall) − rabatter + fraktinntekter |
| 3 | Trekk fra 25% MVA for å få omsetning eksl. MVA |
| 4 | Koble produktnavn med innkjøpspris fra Google Sheets |
| 5 | Beregn varekostnad: innkjøpspris × antall solgt |
| 6 | Hent faktiske kortgebyrer fra Shopify Payments API (per transaksjon) |
| 7 | Hent faktiske Vipps-gebyrer fra Vipps Report API (per dag fra ledger) |
| 8 | Legg til faste ordrekostnader (emballasje, frakt, klistremerker, osv.) |
| 9 | Beregn faste månedlige kostnader fordelt over perioden |
| 10 | Netto resultat = Omsetning eksl. MVA − Varekostnad − Gebyrer − Ordrekostnader − Faste kostnader |

**Transaksjonsgebyrer:**
- **Kort (Shopify Payments):** Faktiske gebyrer hentet per transaksjon. Gjennomsnitt ~2.35%.
- **Vipps:** Faktiske gebyrer hentet per dag fra Vipps Report API. Gjennomsnitt ~2.66%.
- Ingen estimater brukes — alle gebyrer er reelle data fra betalingsleverandørene.

**Rabatthåndtering:**
- Rabatter håndteres korrekt uavhengig av om Shopify distribuerer dem til linjenivå eller ordre-nivå.
- Fri frakt-rabatter og prosentkoder håndteres uten dobbeltelling.

**Oppdatering:**
- Data caches i 1 time. Klikk "Oppdater data" i sidepanelet for å tvinge ny lasting.
- Endringer i Google Sheets reflekteres ved neste oppdatering.
- Nye bestillinger fra Shopify vises automatisk.
- Vipps-gebyrer oppdateres daglig (fees publiseres med 1-2 dagers forsinkelse).
        """)

    with st.expander("Faner i dashboardet"):
        st.markdown("""
| Fane | Innhold |
|------|---------|
| 💰 Økonomi | Kostnadsfordeling, transaksjonsgebyrer, sammendrag, produkter uten kostnadsdata |
| 📈 Trender | Ukentlig fortjeneste, gjennomsnittlig ordreverdi, daglig ordretakt |
| 🛍️ Produkter | Margin per produkt, bestselgere (volum), bidragsmargin |
| 👥 Kunder | Gjentakende kunder, kjøpsmønster (ukedag/tid), betalingsmetode, geografi |
| 🏷️ Rabatter | Rabattkoder oversikt, rabattlønnsomhet |
| 📦 Frakt | Fraktinntekter vs. fraktkostnader |
| 📊 Break-even | Antall bestillinger per måned for å dekke faste kostnader |
| 📋 Bestillinger | Alle bestillinger med detaljer, linjeinformasjon |
| ℹ️ Om | Denne siden — arkitektur og forklaring |
        """)



st.sidebar.caption(f"Butikk: {SHOP}.myshopify.com")


