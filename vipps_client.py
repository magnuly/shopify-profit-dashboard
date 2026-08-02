import requests
from datetime import date, timedelta


def get_vipps_access_token(client_id: str, client_secret: str, subscription_key: str, msn: str) -> str:
    """Get a Vipps API access token (expires in 15 minutes)."""
    resp = requests.post(
        "https://api.vipps.no/accesstoken/get",
        headers={
            "Content-Type": "application/json",
            "client_id": client_id,
            "client_secret": client_secret,
            "Ocp-Apim-Subscription-Key": subscription_key,
            "Merchant-Serial-Number": msn,
            "Vipps-System-Name": "nariz-dashboard",
            "Vipps-System-Version": "1.0.0",
        },
        data="",
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_vipps_fees(
    access_token: str,
    subscription_key: str,
    msn: str,
    ledger_id: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict:
    """Fetch actual Vipps transaction fees from the Report API.

    Returns a dict with:
        - total_fees: total fees in NOK
        - total_captured: total captured amount in NOK
        - avg_rate: average fee rate as decimal (e.g., 0.0266)
        - fee_entries: list of individual fee entries
    """
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=90)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Ocp-Apim-Subscription-Key": subscription_key,
        "Merchant-Serial-Number": msn,
    }

    all_fees = []
    all_captures = []
    current = start_date

    while current <= end_date:
        date_str = current.strftime("%Y-%m-%d")

        # Fees
        resp_fees = requests.get(
            f"https://api.vipps.no/report/v2/ledgers/{ledger_id}/fees/dates/{date_str}",
            headers=headers,
        )
        if resp_fees.status_code == 200:
            for item in resp_fees.json().get("items", []):
                if item.get("entryType") == "capture-fee":
                    all_fees.append(item)

        # Funds (captures) for total amount
        resp_funds = requests.get(
            f"https://api.vipps.no/report/v2/ledgers/{ledger_id}/funds/dates/{date_str}",
            headers=headers,
        )
        if resp_funds.status_code == 200:
            for item in resp_funds.json().get("items", []):
                if item.get("entryType") == "capture":
                    all_captures.append(item)

        current += timedelta(days=1)

    # Amounts are in øre (minor units), convert to NOK
    total_fees_nok = sum(abs(f["amount"]) for f in all_fees) / 100
    total_captured_nok = sum(f["amount"] for f in all_captures) / 100
    avg_rate = (total_fees_nok / total_captured_nok) if total_captured_nok > 0 else 0

    return {
        "total_fees": total_fees_nok,
        "total_captured": total_captured_nok,
        "avg_rate": avg_rate,
        "num_transactions": len(all_captures),
        "fee_entries": all_fees,
    }


def get_vipps_ledger_id(access_token: str, subscription_key: str, msn: str) -> str:
    """Get the primary ledger ID for the merchant."""
    resp = requests.get(
        "https://api.vipps.no/settlement/v1/ledgers",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Ocp-Apim-Subscription-Key": subscription_key,
            "Merchant-Serial-Number": msn,
        },
    )
    resp.raise_for_status()
    ledgers = resp.json().get("items", [])
    if ledgers:
        return ledgers[0]["ledgerId"]
    return ""
