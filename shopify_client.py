import requests
import time


def get_access_token(client_id: str, client_secret: str, shop: str) -> str:
    """Request a fresh access token using client credentials."""
    url = f"https://{shop}.myshopify.com/admin/oauth/access_token"
    resp = requests.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_all_orders(
    access_token: str, shop: str, api_version: str = "2024-10"
) -> list[dict]:
    """Fetch all orders with pagination and rate limit handling."""
    headers = {"X-Shopify-Access-Token": access_token}
    url = f"https://{shop}.myshopify.com/admin/api/{api_version}/orders.json?status=any&limit=250"

    all_orders = []
    while url:
        resp = requests.get(url, headers=headers)

        # Handle rate limiting
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", 2))
            time.sleep(retry_after)
            continue

        resp.raise_for_status()
        data = resp.json()
        all_orders.extend(data.get("orders", []))

        # Pagination via Link header
        url = resp.links.get("next", {}).get("url")

    return all_orders


def extract_line_items(orders: list[dict]) -> list[dict]:
    """Flatten orders into individual line items with relevant fields."""
    items = []
    for order in orders:
        order_number = order["order_number"]
        order_date = order["created_at"][:10]
        currency = order["currency"]
        financial_status = order.get("financial_status", "")

        # Payment method (order-level)
        gateways = order.get("payment_gateway_names", [])
        payment_method = gateways[0] if gateways else "Ukjent"

        # Shipping city (order-level), fall back to billing address
        shipping_address = order.get("shipping_address") or {}
        billing_address = order.get("billing_address") or {}
        city = shipping_address.get("city") or billing_address.get("city") or "Ukjent"

        for li in order.get("line_items", []):
            title = li.get("title", "")
            variant_title = li.get("variant_title", "")

            # Build the combined product name for matching with cost sheet
            if variant_title:
                product_key = f"{title} {variant_title}"
            else:
                product_key = title

            items.append(
                {
                    "order_number": order_number,
                    "order_date": order_date,
                    "product_key": product_key,
                    "product_title": title,
                    "variant_title": variant_title,
                    "quantity": li.get("quantity", 0),
                    "unit_price": float(li.get("price", 0)),
                    "total_discount": float(li.get("total_discount", 0)),
                    "currency": currency,
                    "financial_status": financial_status,
                    "product_id": li.get("product_id"),
                    "variant_id": li.get("variant_id"),
                    "payment_method": payment_method,
                    "city": city,
                }
            )
    return items
