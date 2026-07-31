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
        customer_email = order.get("email", "")

        # Payment method (order-level)
        gateways = order.get("payment_gateway_names", [])
        payment_method = gateways[0] if gateways else "Ukjent"

        # Shipping city (order-level), fall back to billing address
        shipping_address = order.get("shipping_address") or {}
        billing_address = order.get("billing_address") or {}
        city = shipping_address.get("city") or billing_address.get("city") or "Ukjent"

        # Order-level discount (percentage/fixed codes applied at checkout)
        order_total_discount = float(order.get("total_discounts", 0))

        # Discount codes used
        discount_codes = order.get("discount_codes", [])
        discount_code = discount_codes[0]["code"] if discount_codes else "Ingen rabatt"

        # Shipping revenue (what customer paid for shipping)
        shipping_revenue = sum(
            float(sl.get("price", 0)) for sl in order.get("shipping_lines", [])
        )

        # Calculate total item value for proportional discount distribution
        line_items = order.get("line_items", [])
        total_items_value = sum(
            float(li.get("price", 0)) * li.get("quantity", 0) for li in line_items
        )

        for li in line_items:
            title = li.get("title", "")
            variant_title = li.get("variant_title", "")

            # Build the combined product name for matching with cost sheet
            if variant_title:
                product_key = f"{title} {variant_title}"
            else:
                product_key = title

            item_value = float(li.get("price", 0)) * li.get("quantity", 0)
            # Per-line-item discount (direct item discounts)
            item_discount = float(li.get("total_discount", 0))
            # Proportional share of order-level discount
            if total_items_value > 0:
                order_discount_share = order_total_discount * (item_value / total_items_value)
            else:
                order_discount_share = 0

            items.append(
                {
                    "order_number": order_number,
                    "order_date": order_date,
                    "product_key": product_key,
                    "product_title": title,
                    "variant_title": variant_title,
                    "quantity": li.get("quantity", 0),
                    "unit_price": float(li.get("price", 0)),
                    "item_discount": item_discount,
                    "order_discount_share": order_discount_share,
                    "total_discount": item_discount + order_discount_share,
                    "shipping_revenue": 0.0,  # assigned to first item only
                    "discount_code": discount_code,
                    "currency": currency,
                    "financial_status": financial_status,
                    "product_id": li.get("product_id"),
                    "variant_id": li.get("variant_id"),
                    "payment_method": payment_method,
                    "city": city,
                    "customer_email": customer_email,
                }
            )

        # Add shipping revenue to the first line item of this order
        if items and shipping_revenue > 0:
            # Find the last N items we just added (belonging to this order)
            items[-len(line_items)]["shipping_revenue"] = shipping_revenue

    return items
