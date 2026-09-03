import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_cost_data(
    service_account_info: dict,
    spreadsheet_id: str,
    sheet_index: int = 0,
) -> pd.DataFrame:
    """Read cost data from Google Sheets and return as DataFrame."""
    creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    gc = gspread.authorize(creds)

    spreadsheet = gc.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.get_worksheet(sheet_index)

    records = worksheet.get_all_records()
    df = pd.DataFrame(records)

    # Rename columns to standard English names for internal use
    df = df.rename(
        columns={
            "Produkt navn": "product_key",
            "Innkjøpspris pr. produkt i NOK uten MVA": "cost_price",
        }
    )

    # If rename didn't catch the column (encoding), try the second column by position
    if "cost_price" not in df.columns and len(df.columns) >= 2:
        df.columns = ["product_key"] + ["cost_price"] + list(df.columns[2:])

    # Clean cost_price: handle empty strings, convert to numeric
    df["cost_price"] = pd.to_numeric(df["cost_price"], errors="coerce")

    # Strip whitespace from product keys
    df["product_key"] = df["product_key"].str.strip()

    # Remove empty rows (no product name)
    df = df[df["product_key"].str.len() > 0]

    return df


def get_overhead_costs(
    service_account_info: dict,
    spreadsheet_id: str,
    sheet_index: int = 1,
) -> dict:
    """Read fixed monthly overhead costs from the 'Faste avgifter' tab."""
    creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    gc = gspread.authorize(creds)

    spreadsheet = gc.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.get_worksheet(sheet_index)
    rows = worksheet.get_all_values()

    def parse_kr(value: str) -> float:
        """Parse Norwegian currency strings like '2 000,00 kr' or '266,75 kr'."""
        cleaned = value.replace("kr", "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    # Parse fixed monthly costs (column A=name, B=amount)
    # Skip header row (row 0) and sum row
    fixed_costs = {}
    for row in rows[1:]:  # skip header
        name = row[0].strip() if row[0] else ""
        amount_str = row[1].strip() if len(row) > 1 else ""
        if name and name.lower() != "sum" and amount_str:
            fixed_costs[name] = parse_kr(amount_str)

    return {
        "fixed_monthly": fixed_costs,
        "fixed_monthly_total": sum(fixed_costs.values()),
    }


def get_per_order_costs(
    service_account_info: dict,
    spreadsheet_id: str,
    sheet_index: int = 2,
) -> dict:
    """Read per-order costs from the 'Avgifter pr. bestilling' tab."""
    creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    gc = gspread.authorize(creds)

    spreadsheet = gc.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.get_worksheet(sheet_index)
    rows = worksheet.get_all_values()

    transaction_fees = {}
    fixed_per_order = {}

    for row in rows[1:]:  # skip header
        name = row[0].strip() if row[0] else ""
        value_str = row[1].strip() if len(row) > 1 else ""

        if not name or not value_str:
            continue

        # Parse transaction fees (percentage-based)
        if "transaksjonsgebyr" in name.lower():
            # Parse "2% av ordrepris" or "5% av ordrepris + 2 kr"
            pct = 0.0
            fixed = 0.0
            if "%" in value_str:
                pct_part = value_str.split("%")[0].replace(",", ".").strip()
                try:
                    pct = float(pct_part) / 100
                except ValueError:
                    pass
            if "+" in value_str:
                fixed_part = value_str.split("+")[1].replace("kr", "").replace(",", ".").strip()
                try:
                    fixed = float(fixed_part)
                except ValueError:
                    pass

            if "vipps" in name.lower():
                transaction_fees["vipps"] = {"rate": pct, "fixed": fixed}
            else:
                transaction_fees["kort"] = {"rate": pct, "fixed": fixed}
        else:
            # Fixed per-order cost
            cleaned = value_str.replace(",", ".").strip()
            try:
                amount = float(cleaned)
                fixed_per_order[name] = amount
            except ValueError:
                pass

    return {
        "transaction_fees": transaction_fees,
        "fixed_per_order": fixed_per_order,
        "fixed_per_order_total": sum(fixed_per_order.values()),
    }


def get_dashboard_cost_data(service_account_info: dict, spreadsheet_id: str) -> tuple[pd.DataFrame, dict, dict]:
    """Load all three dashboard worksheets using one Google authorization.

    This avoids creating three service-account clients and opening the same
    spreadsheet three times whenever the dashboard cache is refreshed.
    """
    creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
    spreadsheet = gspread.authorize(creds).open_by_key(spreadsheet_id)

    product_rows = spreadsheet.get_worksheet(0).get_all_records()
    costs_df = pd.DataFrame(product_rows)
    if "cost_price" not in costs_df.columns and len(costs_df.columns) >= 2:
        costs_df.columns = ["product_key", "cost_price"] + list(costs_df.columns[2:])
    else:
        costs_df = costs_df.rename(columns={
            "Produkt navn": "product_key",
            "Innkjøpspris pr. produkt i NOK uten MVA": "cost_price",
        })
    costs_df["cost_price"] = pd.to_numeric(costs_df["cost_price"], errors="coerce")
    costs_df["product_key"] = costs_df["product_key"].str.strip()
    costs_df = costs_df[costs_df["product_key"].str.len() > 0]

    def parse_kr(value: str) -> float:
        cleaned = value.replace("kr", "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
        try:
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0

    fixed_costs = {}
    for row in spreadsheet.get_worksheet(1).get_all_values()[1:]:
        name = row[0].strip() if row else ""
        amount = row[1].strip() if len(row) > 1 else ""
        if name and name.lower() != "sum" and amount:
            fixed_costs[name] = parse_kr(amount)
    overhead = {
        "fixed_monthly": fixed_costs,
        "fixed_monthly_total": sum(fixed_costs.values()),
    }

    transaction_fees = {}
    fixed_per_order = {}
    for row in spreadsheet.get_worksheet(2).get_all_values()[1:]:
        name = row[0].strip() if row else ""
        value = row[1].strip() if len(row) > 1 else ""
        if not name or not value:
            continue
        if "transaksjonsgebyr" in name.lower():
            rate = 0.0
            fixed = 0.0
            if "%" in value:
                try:
                    rate = float(value.split("%")[0].replace(",", ".").strip()) / 100
                except ValueError:
                    pass
            if "+" in value:
                fixed = parse_kr(value.split("+", 1)[1])
            transaction_fees["vipps" if "vipps" in name.lower() else "kort"] = {"rate": rate, "fixed": fixed}
        else:
            fixed_per_order[name] = parse_kr(value)
    per_order = {
        "transaction_fees": transaction_fees,
        "fixed_per_order": fixed_per_order,
        "fixed_per_order_total": sum(fixed_per_order.values()),
    }
    return costs_df, overhead, per_order
