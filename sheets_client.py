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

    # Clean cost_price: handle empty strings, convert to numeric
    df["cost_price"] = pd.to_numeric(df["cost_price"], errors="coerce")

    # Strip whitespace from product keys
    df["product_key"] = df["product_key"].str.strip()

    return df
