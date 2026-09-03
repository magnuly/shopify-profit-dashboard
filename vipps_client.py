import requests
from concurrent.futures import ThreadPoolExecutor
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
        # The dashboard uses this as a current actual fee rate. Thirty days is
        # representative while avoiding unnecessary historical API traffic on
        # every manual refresh.
        start_date = end_date - timedelta(days=30)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Ocp-Apim-Subscription-Key": subscription_key,
        "Merchant-Serial-Number": msn,
    }

    dates = []
    current = start_date
    while current <= end_date:
        dates.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    def fetch_day(date_str: str) -> tuple[list[dict], list[dict]]:
        """Fetch one day's fees and captures. API amounts are in øre."""
        fees_response = requests.get(
            f"https://api.vipps.no/report/v2/ledgers/{ledger_id}/fees/dates/{date_str}",
            headers=headers,
            timeout=20,
        )
        funds_response = requests.get(
            f"https://api.vipps.no/report/v2/ledgers/{ledger_id}/funds/dates/{date_str}",
            headers=headers,
            timeout=20,
        )

        fees = []
        captures = []
        if fees_response.status_code == 200:
            fees = [
                item for item in fees_response.json().get("items", [])
                if item.get("entryType") == "capture-fee"
            ]
        if funds_response.status_code == 200:
            captures = [
                item for item in funds_response.json().get("items", [])
                if item.get("entryType") == "capture"
            ]
        return fees, captures

    # The former sequential implementation made two requests for each of 90
    # dates. Eight workers preserve a modest API load while cutting refresh
    # latency substantially.
    all_fees = []
    all_captures = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        for fees, captures in executor.map(fetch_day, dates):
            all_fees.extend(fees)
            all_captures.extend(captures)

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
