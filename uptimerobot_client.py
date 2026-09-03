"""Read-only client for UptimeRobot's v3 API."""

from datetime import datetime, timezone

import requests


API_BASE_URL = "https://api.uptimerobot.com/v3"


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def get_monitor(api_key: str, monitor_url: str) -> dict | None:
    """Return the monitor matching *monitor_url*, or None when not found."""
    response = requests.get(
        f"{API_BASE_URL}/monitors",
        params={"url": monitor_url, "limit": 50},
        headers=_headers(api_key),
        timeout=20,
    )
    response.raise_for_status()
    monitors = response.json().get("data", [])
    return monitors[0] if monitors else None


def get_incidents(api_key: str, monitor_id: str | int) -> list[dict]:
    """Return all incidents for a monitor, following the cursor pagination."""
    incidents: list[dict] = []
    cursor: str | None = None

    while True:
        params = {"monitorId": monitor_id, "limit": 100}
        if cursor:
            params["cursor"] = cursor

        response = requests.get(
            f"{API_BASE_URL}/incidents",
            params=params,
            headers=_headers(api_key),
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        incidents.extend(payload.get("data", []))
        cursor = payload.get("nextLink")
        if not cursor:
            break

    return incidents


def as_utc_datetime(value: str | int | float | None) -> datetime | None:
    """Convert common API timestamps to timezone-aware UTC datetimes."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
