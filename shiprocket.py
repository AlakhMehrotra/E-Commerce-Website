"""
Shiprocket courier integration.

Design:
- Auth is email+password (there is no separate API key for Shiprocket) —
  POST /auth/login returns a bearer token valid ~10 days. That token is
  cached in app_settings (reusing the existing key-value table) so a fresh
  order doesn't re-authenticate every time.
- create_shipment() is the single entry point, called right after an order
  is confirmed (COD placement, or Razorpay verify/webhook — whichever gets
  there first). It creates the order on Shiprocket's side, then immediately
  requests AWB auto-assignment and pickup, so the courier process starts
  with no manual admin step.
- The checkout form here only collects name/phone/address/pincode (no city
  or state), but Shiprocket requires city+state. Rather than changing the
  checkout form, city/state are looked up for free from the pincode via
  India Post's public API (api.postalpincode.in, no key required).
- Every call is best-effort: app.py wraps this in a try/except so a
  Shiprocket hiccup (bad credentials, their API being down, etc.) never
  blocks or fails the customer's checkout.
"""
import os
import time
import logging
import requests

import database as db

logger = logging.getLogger("jeevani.shiprocket")

SHIPROCKET_EMAIL = os.environ.get("SHIPROCKET_EMAIL", "")
SHIPROCKET_PASSWORD = os.environ.get("SHIPROCKET_PASSWORD", "")
SHIPROCKET_PICKUP_LOCATION = os.environ.get("SHIPROCKET_PICKUP_LOCATION", "Primary")

# Package defaults — sarees are light & flat. Override via env vars if your
# packaging differs (e.g. bridal sarees with heavier boxing).
PACKAGE_WEIGHT_KG = float(os.environ.get("SHIPROCKET_PACKAGE_WEIGHT_KG", "0.5"))
PACKAGE_LENGTH_CM = float(os.environ.get("SHIPROCKET_PACKAGE_LENGTH_CM", "30"))
PACKAGE_BREADTH_CM = float(os.environ.get("SHIPROCKET_PACKAGE_BREADTH_CM", "25"))
PACKAGE_HEIGHT_CM = float(os.environ.get("SHIPROCKET_PACKAGE_HEIGHT_CM", "5"))

BASE_URL = "https://apiv2.shiprocket.in/v1/external"

SHIPROCKET_ENABLED = bool(SHIPROCKET_EMAIL and SHIPROCKET_PASSWORD)

_TOKEN_TTL_SECONDS = 9 * 24 * 3600  # refresh a day early of Shiprocket's ~10-day expiry


def _get_token(conn):
    """Returns a cached auth token if still fresh, otherwise logs in again."""
    cached = db.get_setting(conn, "shiprocket_token", "")
    cached_at = db.get_setting(conn, "shiprocket_token_at", "0")
    try:
        is_fresh = cached and (time.time() - float(cached_at)) < _TOKEN_TTL_SECONDS
    except ValueError:
        is_fresh = False
    if is_fresh:
        return cached

    res = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": SHIPROCKET_EMAIL, "password": SHIPROCKET_PASSWORD},
        timeout=15,
    )
    res.raise_for_status()
    token = res.json()["token"]
    db.set_setting(conn, "shiprocket_token", token)
    db.set_setting(conn, "shiprocket_token_at", str(time.time()))
    return token


def _lookup_city_state(pincode):
    """Free, keyless pincode -> (city, state) lookup via India Post's public
    API. Falls back to ('', '') if the lookup fails for any reason — the
    Shiprocket order create call still gets attempted with blanks rather
    than blocking the whole shipment on this one lookup."""
    try:
        res = requests.get(f"https://api.postalpincode.in/pincode/{pincode}", timeout=3)
        res.raise_for_status()
        data = res.json()
        if data and data[0].get("Status") == "Success" and data[0].get("PostOffice"):
            po = data[0]["PostOffice"][0]
            return po.get("District", ""), po.get("State", "")
    except Exception:
        logger.warning("Pincode lookup fallback for %s", pincode)
    return "", ""


def create_shipment(order, items, conn, customer_email=""):
    """order: dict from database.row_to_order(). items: list from
    database.get_order_items(). Creates the order on Shiprocket, then
    auto-assigns a courier + AWB and requests pickup. Returns a dict
    describing what succeeded; raises on the initial order-create call
    failing so the caller's except-block can log it."""
    token = _get_token(conn)
    headers = {"Authorization": f"Bearer {token}"}

    city, state = _lookup_city_state(order["customerPincode"])

    is_cod = order["paymentMethod"] == "cod"
    order_items = [
        {
            "name": it["name"][:150],
            "sku": f"SKU-{it.get('productId') or 'NA'}",
            "units": it["quantity"],
            "selling_price": it["price"],
        }
        for it in items
    ]

    order_date = (order.get("createdAt") or "")[:19].replace("T", " ")

    payload = {
        "order_id": order["orderNumber"],
        "order_date": order_date,
        "pickup_location": SHIPROCKET_PICKUP_LOCATION,
        "billing_customer_name": order["customerName"],
        "billing_last_name": "",
        "billing_address": order["customerAddress"],
        "billing_city": city or "Varanasi",
        "billing_pincode": order["customerPincode"],
        "billing_state": state or "Uttar Pradesh",
        "billing_country": "India",
        "billing_email": customer_email or "orders@shrijeevanisarees.example",
        "billing_phone": order["customerPhone"],
        "shipping_is_billing": True,
        "order_items": order_items,
        "payment_method": "COD" if is_cod else "Prepaid",
        "sub_total": order["subtotal"],
        "length": PACKAGE_LENGTH_CM,
        "breadth": PACKAGE_BREADTH_CM,
        "height": PACKAGE_HEIGHT_CM,
        "weight": PACKAGE_WEIGHT_KG,
    }

    res = requests.post(f"{BASE_URL}/orders/create/adhoc", json=payload, headers=headers, timeout=20)
    res.raise_for_status()
    data = res.json()
    shipment_id = data.get("shipment_id")

    result = {
        "success": True,
        "shiprocket_order_id": data.get("order_id"),
        "shipment_id": shipment_id,
        "awb": None,
        "courier_name": None,
        "pickup_requested": False,
    }

    if not shipment_id:
        return result

    # Auto-assign the best available courier + AWB. Best-effort: the order
    # itself is already created on Shiprocket even if this sub-step fails,
    # and can be assigned manually from their dashboard.
    try:
        awb_res = requests.post(
            f"{BASE_URL}/courier/assign/awb",
            json={"shipment_id": shipment_id},
            headers=headers,
            timeout=20,
        )
        awb_res.raise_for_status()
        awb_data = (awb_res.json().get("response", {}) or {}).get("data", {}) or {}
        result["awb"] = awb_data.get("awb_code")
        result["courier_name"] = awb_data.get("courier_name")
    except Exception:
        logger.exception("Shiprocket AWB auto-assign failed for shipment %s", shipment_id)

    # Request pickup so the courier is scheduled to collect the parcel —
    # this is the step that actually "starts" physical delivery.
    try:
        pickup_res = requests.post(
            f"{BASE_URL}/courier/generate/pickup",
            json={"shipment_id": [shipment_id]},
            headers=headers,
            timeout=20,
        )
        pickup_res.raise_for_status()
        result["pickup_requested"] = True
    except Exception:
        logger.exception("Shiprocket pickup request failed for shipment %s", shipment_id)

    return result
