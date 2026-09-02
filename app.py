"""
Shri Jeevani Sarees — Flask backend
================================================
Serves the existing frontend (saree.html / saree.css / saree.js / admin.html)
exactly as-is, and exposes a REST API backed by SQLite for:
  - Product catalogue (list / detail / create / update / delete)
  - Secure admin authentication (hashed password + server-side session)
  - Phase 2 CHANGE: Customer accounts (signup/login/logout/session)
  - Phase 2 CHANGE: Server-persisted cart (per logged-in customer)
  - Phase 2 CHANGE: Wishlist (per logged-in customer)

Run:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000
"""
import os
from dotenv import load_dotenv
load_dotenv()
load_dotenv("a.env")
import re
import time
import datetime
import sqlite3
import hmac
import hashlib
import secrets
import json
import html as html_lib
from functools import wraps

import requests
from flask import Flask, jsonify, request, session, send_from_directory, Response
from werkzeug.security import check_password_hash, generate_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import database as db
import mail as mailer
import shiprocket as shiprocket_service

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
app.config["SECRET_KEY"] = os.environ.get("JEEVANI_SECRET_KEY", "dev-secret-change-this-in-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Security fix: only send the session cookie over HTTPS. Set
# JEEVANI_INSECURE_COOKIE=true only for local http://localhost development.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("JEEVANI_INSECURE_COOKIE", "false").lower() != "true"
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB request cap (supports multiple photos + JSON overhead)

# Phase 4 CHANGE: SMTP email — order confirmation, shipping confirmation,
# password reset. See README.md for the MAIL_* environment variables.
mailer.init_mail(app)

# Phase 5 CHANGE: base URL used to build canonical links, sitemap URLs, and
# Open Graph URLs. Set this to your real domain in production (e.g.
# https://shrijeevanisarees.com) — it defaults to the same local URL mail.py
# already falls back to, so only one env var needs setting either way.
SITE_URL = app.config.get("SITE_URL", os.environ.get("SITE_URL", "http://localhost:5000")).rstrip("/")

VALID_CATEGORIES = {"bridal", "festive", "classic", "contemporary"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# ─────────────────────────────────────────────────────────────────────────
# Security fix: brute-force protection for login endpoints.
# Simple in-memory sliding-window limiter — good enough for a single-process
# deployment consistent with this project's "no extra infra" philosophy.
# For a multi-worker/gunicorn deployment, swap this for a shared store
# (e.g. Redis) since in-memory state isn't shared across processes.
# ─────────────────────────────────────────────────────────────────────────
import threading
import collections

_LOGIN_ATTEMPTS = collections.defaultdict(list)  # key -> [timestamps]
_LOGIN_LOCK = threading.Lock()
LOGIN_RATE_LIMIT = 8          # max attempts
LOGIN_RATE_WINDOW = 300       # per 5 minutes


def rate_limited(key):
    """Returns True (and records the attempt) if this key is currently
    rate-limited. Always records the attempt so repeated hammering keeps
    extending the lockout window."""
    now = time.time()
    with _LOGIN_LOCK:
        attempts = _LOGIN_ATTEMPTS[key]
        attempts[:] = [t for t in attempts if now - t < LOGIN_RATE_WINDOW]
        if len(attempts) >= LOGIN_RATE_LIMIT:
            attempts.append(now)
            return True
        attempts.append(now)
        return False


def login_rate_key(identifier):
    # Keyed on client IP + submitted identifier, so an attacker can't lock
    # out a legitimate user just by spamming failed attempts against their
    # username from a different IP, while still throttling any single
    # IP/identifier combination doing credential stuffing.
    return f"{request.remote_addr}:{(identifier or '').lower()}"

# Phase 3 CHANGE: Razorpay — set these two env vars in production. Until
# they're set, the storefront still works fully on Cash on Delivery; the
# UPI/Card options simply tell the customer online payment isn't configured.
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_ENABLED = bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)
VALID_ORDER_STATUSES = {"placed", "shipped", "delivered", "cancelled"}

# CHANGE: Payment-method-based pricing. COD carries a small handling
# surcharge (cash handling / higher RTO risk for the courier); prepaid
# (UPI/Card) orders get a discount as an incentive to pay online. Both are
# applied inside build_order_from_cart() so every order-creation path (COD
# checkout, Razorpay create/verify, webhook) stays in sync automatically.
COD_EXTRA_CHARGE = 50          # ₹ added to shipping for Cash on Delivery
ONLINE_PAYMENT_DISCOUNT_PERCENT = 10   # % off subtotal for UPI/Card


def compute_shipping_cost(subtotal, payment_method="cod"):
    """Return shipping cost with a COD handling fee but no base shipping."""
    shipping = 0
    if (payment_method or "cod").lower() == "cod":
        shipping += COD_EXTRA_CHARGE
    return shipping


# CHANGE: One-time return/replacement policy. A customer gets a single
# combined allowance per order — either one replacement OR one return,
# whichever they request first — enforced via orders.replacement_used.
VALID_RETURN_REQUEST_TYPES = {"replace", "return"}
VALID_RETURN_REQUEST_STATUSES = {"pending", "approved", "rejected", "completed"}


# ─────────────────────────────────────────────────────────────────────────
# Phase 5 CHANGE: SEO — server-rendered meta tags per route.
#
# The frontend is still a single static SPA shell (saree.html) — that
# doesn't change. What changes is that Flask now serves that shell through
# a small templating step instead of send_from_directory, swapping in a
# per-route <title>/description/canonical/Open Graph block before sending
# it. This is what makes a shared product link unfurl correctly on
# WhatsApp/Twitter/etc, and gives search engines a distinct, indexable URL
# and description per product instead of one generic page for the whole
# catalogue.
# ─────────────────────────────────────────────────────────────────────────
DEFAULT_TITLE = "Shri Jeewani Saree Center - Exquisite Banarasi Silk Sarees"
DEFAULT_DESCRIPTION = (
    "Authentic handwoven Banarasi silk sarees from Varanasi — bridal, festive, "
    "classic and contemporary collections, crafted by skilled artisans on the ghats of Kashi."
)
# Falls back to the hero photo already used on the homepage (publicly hosted
# on Wikimedia Commons) since product photos are stored as base64 data URLs,
# which social platforms and crawlers can't fetch as an og:image.
DEFAULT_OG_IMAGE = "https://commons.wikimedia.org/wiki/Special:FilePath/Kashi%20Vishwanath%20Temple%20in%202025.jpg"

_file_cache = {}


def _load_cached(filename):
    path = os.path.join(BASE_DIR, filename)
    mtime = os.path.getmtime(path)
    cached = _file_cache.get(filename)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    _file_cache[filename] = (mtime, content)
    return content


def render_seo_shell(path, *, title=None, description=None, og_image=None, robots=None, initial_state=None, status=200):
    content = _load_cached("saree.html")
    canonical = f"{SITE_URL}{path}"

    content = content.replace("__CANONICAL_URL__", canonical)
    content = content.replace("__OG_IMAGE_URL__", html_lib.escape(og_image or DEFAULT_OG_IMAGE, quote=True))
    content = content.replace("__SITE_URL__", SITE_URL)

    if title:
        content = content.replace(DEFAULT_TITLE, html_lib.escape(title))
    if description:
        content = content.replace(DEFAULT_DESCRIPTION, html_lib.escape(description))
    if robots:
        content = content.replace('content="index, follow"', f'content="{robots}"')

    if initial_state is not None:
        script = f'<script>window.__INITIAL_STATE__ = {json.dumps(initial_state)};</script>\n    <script src="saree.js">'
        content = content.replace('<script src="saree.js">', script, 1)

    return Response(content, status=status, mimetype="text/html")


@app.route("/")
def serve_home():
    return render_seo_shell("/")


@app.route("/saree.html")
def serve_saree():
    return render_seo_shell("/")


@app.route("/admin.html")
def serve_admin():
    return send_from_directory(BASE_DIR, "admin.html")


# Phase 5 CHANGE: dynamic, SEO-friendly product URLs — /product/<slug>
# instead of everything living behind one client-side modal with no URL of
# its own. Crawlers and shared links now get a real title/description/OG
# image for that specific saree; real visitors get the same interactive SPA,
# just booted straight into that product's detail view via
# window.__INITIAL_STATE__ (saree.js reads this — see openProduct()).
@app.route("/product/<slug>")
def serve_product(slug):
    conn = db.get_db()
    row = conn.execute("SELECT * FROM products WHERE slug = ? AND is_active = 1", (slug,)).fetchone()
    if not row:
        conn.close()
        return render_seo_shell(
            f"/product/{slug}",
            title=f"Product not found — {DEFAULT_TITLE}",
            description="This product is no longer available. Browse our full collection instead.",
            robots="noindex, follow",
            status=404,
        )

    product = db.row_to_product(row)
    conn.close()

    price_str = f"₹{product['price']:,}"
    description = product["description"] or DEFAULT_DESCRIPTION
    og_image = product["imageData"] if (product["imageData"] or "").startswith("http") else None

    return render_seo_shell(
        f"/product/{slug}",
        title=f"{product['name']} — {price_str} | Shri Jeewani Saree Center",
        description=f"{description} {price_str} · {product['fabric']}.".strip(),
        og_image=og_image,
        initial_state={"productSlug": slug},
    )


# ─────────────────────────────────────────────────────────────────────────
# Phase 5 CHANGE: robots.txt + sitemap.xml
# ─────────────────────────────────────────────────────────────────────────
@app.route("/robots.txt")
def robots_txt():
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin.html\n"
        "Disallow: /api/\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    conn = db.get_db()
    products = conn.execute(
        "SELECT slug, updated_at FROM products WHERE is_active = 1 ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()

    static_urls = [
        {"loc": "/", "changefreq": "daily", "priority": "1.0"},
        {"loc": "/#collections", "changefreq": "daily", "priority": "0.9"},
        {"loc": "/#about", "changefreq": "monthly", "priority": "0.5"},
        {"loc": "/#contact", "changefreq": "monthly", "priority": "0.5"},
    ]

    entries = [
        f"  <url><loc>{html_lib.escape(SITE_URL + u['loc'])}</loc>"
        f"<changefreq>{u['changefreq']}</changefreq><priority>{u['priority']}</priority></url>"
        for u in static_urls
    ]
    for p in products:
        lastmod = (p["updated_at"] or "")[:10]
        entries.append(
            f"  <url><loc>{html_lib.escape(SITE_URL + '/product/' + p['slug'])}</loc>"
            f"<lastmod>{lastmod}</lastmod><changefreq>weekly</changefreq><priority>0.8</priority></url>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(entries)
        + "\n</urlset>\n"
    )
    return Response(xml, mimetype="application/xml")


# saree.css / saree.js are served automatically by Flask's static handler
# because static_url_path="" maps the project root to "/".


# ─────────────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────────────
def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"error": "Unauthorized. Please log in as admin."}), 401
        return view(*args, **kwargs)
    return wrapped


def customer_required(view):
    """Phase 2 CHANGE: gate for cart/wishlist routes — requires a logged-in
    customer session (separate from the admin session).
    Phase 6 CHANGE: also rejects a session that belongs to an
    admin-blocked account, so a block takes effect immediately across every
    customer-only endpoint, not just sign-in/session-check."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user_id = session.get("user_id")
        if not user_id:
            return jsonify({"error": "Please sign in to continue."}), 401
        conn = db.get_db()
        row = db.get_user_by_id(conn, user_id)
        conn.close()
        if not row or row["is_blocked"]:
            session.pop("user_id", None)
            return jsonify({"error": "This account is no longer active. Please contact support."}), 403
        return view(*args, **kwargs)
    return wrapped


# ─────────────────────────────────────────────────────────────────────────
# Validation helpers
# ─────────────────────────────────────────────────────────────────────────
def validate_product_payload(data, partial=False):
    """Returns (cleaned_dict, error_message). error_message is None if valid."""
    cleaned = {}

    name = (data.get("name") or "").strip()
    if not partial or "name" in data:
        if not name:
            return None, "Product name is required."
        if len(name) > 200:
            return None, "Product name is too long."
        cleaned["name"] = name

    category = (data.get("category") or "").strip().lower()
    if not partial or "category" in data:
        if category not in VALID_CATEGORIES:
            return None, f"Category must be one of: {', '.join(sorted(VALID_CATEGORIES))}."
        cleaned["category"] = category

    if not partial or "price" in data:
        try:
            price = int(data.get("price"))
        except (TypeError, ValueError):
            return None, "Price must be a valid number."
        if price < 0 or price > 100000000:
            return None, "Price must be a realistic positive value."
        cleaned["price"] = price

    if not partial or "description" in data:
        description = (data.get("description") or "").strip()
        if not description:
            return None, "Description is required."
        if len(description) > 4000:
            return None, "Description is too long."
        cleaned["description"] = description

    if "badge" in data:
        badge = (data.get("badge") or "").strip().upper()
        if badge and badge not in {"NEW", "BESTSELLER", "SALE", "PREMIUM"}:
            return None, "Invalid badge value."
        cleaned["badge"] = badge

    if "emoji" in data:
        emoji = (data.get("emoji") or "🌸").strip()[:8]
        cleaned["emoji"] = emoji or "🌸"

    if "fabric" in data:
        cleaned["fabric"] = (data.get("fabric") or "Pure Banarasi Silk").strip()[:200]

    if "color" in data:
        cleaned["color"] = (data.get("color") or "").strip()[:100]

    if "stock" in data:
        try:
            stock = int(data.get("stock"))
            if stock < 0:
                return None, "Stock cannot be negative."
            cleaned["stock"] = stock
        except (TypeError, ValueError):
            return None, "Stock must be a valid number."

    # Support both `images` (array of photos) and legacy `imageData` (single photo)
    images_list = None
    if "images" in data:
        raw_images = data.get("images")
        if raw_images is not None:
            if not isinstance(raw_images, list):
                return None, "Images must be provided as a list."
            if len(raw_images) > 10:
                return None, "Maximum 10 photos allowed per product."
            validated_images = []
            for idx, img in enumerate(raw_images):
                if not isinstance(img, str) or not img.strip():
                    continue
                img_str = img.strip()
                if img_str.startswith("http://") or img_str.startswith("https://") or img_str.startswith("/"):
                    validated_images.append(img_str)
                elif re.match(r"^data:image/(png|jpe?g|webp|gif);base64,", img_str):
                    approx_bytes = len(img_str) * 3 / 4
                    if approx_bytes > MAX_IMAGE_BYTES:
                        return None, f"Photo #{idx+1} is too large. Please choose files under 5 MB."
                    validated_images.append(img_str)
                else:
                    return None, f"Photo #{idx+1} must be a valid PNG, JPG, WEBP, GIF, or image URL."
            images_list = validated_images
            cleaned["images"] = json.dumps(images_list)
            cleaned["image_data"] = images_list[0] if images_list else None

    if "imageData" in data and images_list is None:
        image_data = data.get("imageData") or ""
        if image_data:
            if not (image_data.startswith("http://") or image_data.startswith("https://") or image_data.startswith("/") or re.match(r"^data:image/(png|jpe?g|webp|gif);base64,", image_data)):
                return None, "Image must be a valid PNG, JPG, WEBP, GIF, or URL."
            if not (image_data.startswith("http://") or image_data.startswith("https://") or image_data.startswith("/")):
                approx_bytes = len(image_data) * 3 / 4
                if approx_bytes > MAX_IMAGE_BYTES:
                    return None, "Image is too large. Please choose a file under 5 MB."
            cleaned["image_data"] = image_data
            cleaned["images"] = json.dumps([image_data])
        else:
            cleaned["image_data"] = None
            cleaned["images"] = "[]"

    for flag in ("featured", "bestseller", "newArrival"):
        if flag in data:
            db_field = "new_arrival" if flag == "newArrival" else flag
            cleaned[db_field] = 1 if data.get(flag) else 0

    return cleaned, None


def validate_coupon_payload(data, partial=False):
    """Phase 2 CHANGE: validation for admin coupon create/update."""
    cleaned = {}

    if not partial or "code" in data:
        code = (data.get("code") or "").strip().upper()
        if not code:
            return None, "Coupon code is required."
        if len(code) > 40:
            return None, "Coupon code is too long."
        cleaned["code"] = code

    if not partial or "discountType" in data:
        dtype = (data.get("discountType") or "").strip().lower()
        if dtype not in ("percent", "flat"):
            return None, "Discount type must be 'percent' or 'flat'."
        cleaned["discount_type"] = dtype

    if not partial or "discountValue" in data:
        try:
            value = int(data.get("discountValue"))
        except (TypeError, ValueError):
            return None, "Discount value must be a number."
        if value <= 0:
            return None, "Discount value must be greater than 0."
        cleaned["discount_value"] = value

    if "minOrder" in data:
        try:
            cleaned["min_order"] = max(int(data.get("minOrder") or 0), 0)
        except (TypeError, ValueError):
            return None, "Minimum order must be a number."

    if "maxUses" in data:
        raw = data.get("maxUses")
        if raw in (None, ""):
            cleaned["max_uses"] = None
        else:
            try:
                cleaned["max_uses"] = max(int(raw), 1)
            except (TypeError, ValueError):
                return None, "Max uses must be a number."

    if "expiresAt" in data:
        cleaned["expires_at"] = (data.get("expiresAt") or "").strip() or None

    if "isActive" in data:
        cleaned["is_active"] = 1 if data.get("isActive") else 0

    return cleaned, None


def build_order_from_cart(conn, user_id, data):
    """Phase 2/3 CHANGE: shared order-building logic for both Cash on
    Delivery and Razorpay checkout. Raises ValueError with a customer-facing
    message on any validation failure (empty cart, low stock, bad coupon)."""
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    address = (data.get("address") or "").strip()
    pincode = (data.get("pincode") or "").strip()
    coupon_code = (data.get("couponCode") or "").strip()
    payment_method = (data.get("paymentMethod") or "cod").strip().lower()

    if not name or not phone or not address or not pincode:
        raise ValueError("Please fill in all shipping details.")
    items = db.get_cart_items(conn, user_id)
    if not items and data.get("items"):
        client_items = data.get("items") or []
        for ci in client_items:
            pid = ci.get("productId") or ci.get("id")
            qty = max(int(ci.get("quantity") or 1), 1)
            p_row = conn.execute("SELECT * FROM products WHERE id = ? AND is_active = 1", (pid,)).fetchone()
            if p_row:
                item_dict = dict(p_row)
                item_dict["quantity"] = qty
                item_dict["productId"] = pid
                items.append(item_dict)

    if not items:
        raise ValueError("Your cart is empty.")

    for item in items:
        if item["quantity"] > item["stock"]:
            raise ValueError(f'Only {item["stock"]} unit(s) of "{item["name"]}" left in stock.')

    subtotal = sum(i["price"] * i["quantity"] for i in items)
    tax = round(subtotal * 0.05)
    shipping = compute_shipping_cost(subtotal, payment_method)

    coupon_discount = 0
    coupon_row = None
    if coupon_code:
        coupon_row = db.find_active_coupon(conn, coupon_code)
        if not coupon_row:
            raise ValueError("Invalid or expired coupon code.")
        coupon_discount, coupon_error = db.compute_coupon_discount(coupon_row, subtotal)
        if coupon_error:
            raise ValueError(coupon_error)

    # CHANGE: online-payment incentive discount, stacks with any coupon.
    online_discount = round(subtotal * ONLINE_PAYMENT_DISCOUNT_PERCENT / 100) if payment_method in ("upi", "card") else 0
    discount = coupon_discount + online_discount

    total = max(subtotal + tax + shipping - discount, 0)

    return {
        "name": name, "phone": phone, "address": address, "pincode": pincode,
        "payment_method": payment_method, "items": items,
        "subtotal": subtotal, "tax": tax, "shipping": shipping,
        "discount": discount, "total": total,
        "coupon_row": coupon_row, "coupon_code": coupon_row["code"] if coupon_row else None,
    }


RAZORPAY_PENDING_TTL_SECONDS = 20 * 60  # abandon a pending Razorpay order after 20 minutes


def release_order(conn, row):
    """Shared cleanup for an abandoned/failed/cancelled Razorpay order:
    restock items, restore them to the customer's cart, and give back any
    coupon use. Used by explicit cancel, the webhook, and the stale sweep."""
    items = db.get_order_items(conn, row["id"])
    for item in items:
        conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (item["quantity"], item["productId"]))
        conn.execute(
            """INSERT INTO cart_items (user_id, product_id, quantity) VALUES (?, ?, ?)
               ON CONFLICT(user_id, product_id) DO UPDATE SET quantity = quantity + excluded.quantity""",
            (row["user_id"], item["productId"], item["quantity"]),
        )
    if row["coupon_code"]:
        conn.execute("UPDATE coupons SET used_count = MAX(used_count - 1, 0) WHERE code = ?", (row["coupon_code"],))
    conn.execute("UPDATE orders SET order_status = 'cancelled', payment_status = 'failed' WHERE id = ?", (row["id"],))


def sweep_stale_pending_orders(conn, user_id=None):
    """Reliability/security fix: a Razorpay order that's created but never
    verified (customer closes the tab, loses network, or just abandons
    checkout without triggering the widget's ondismiss) used to hold its
    stock decremented forever, and nothing stopped a customer from repeating
    this to lock out a product's entire inventory. This releases anything
    still 'pending' past RAZORPAY_PENDING_TTL_SECONDS. Called opportunistically
    before creating a new Razorpay order — no separate cron process required."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(seconds=RAZORPAY_PENDING_TTL_SECONDS)).isoformat()
    sql = "SELECT * FROM orders WHERE payment_status = 'pending' AND payment_method != 'cod' AND created_at < ?"
    params = [cutoff]
    if user_id is not None:
        sql += " AND user_id = ?"
        params.append(user_id)
    stale = conn.execute(sql, params).fetchall()
    for row in stale:
        release_order(conn, row)
    if stale:
        conn.commit()


def persist_order(conn, user_id, order_number, built, clear_cart=True):
    """Phase 2/3 CHANGE: decrements stock, writes the order + order_items,
    bumps coupon usage, and clears the cart — all in one transaction.
    Raises ValueError (triggering a rollback by the caller) if a race
    condition drops stock below what's needed between validation and now."""
    for item in built["items"]:
        cur = conn.execute(
            "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
            (item["quantity"], item["id"], item["quantity"]),
        )
        if cur.rowcount == 0:
            raise ValueError(f'"{item["name"]}" just went out of stock. Please update your cart.')

    cur = conn.execute(
        """INSERT INTO orders
           (user_id, order_number, customer_name, customer_phone, customer_address, customer_pincode,
            subtotal, tax, shipping, discount, total, coupon_code, payment_method, payment_status, order_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'placed')""",
        (user_id, order_number, built["name"], built["phone"], built["address"], built["pincode"],
         built["subtotal"], built["tax"], built["shipping"], built["discount"], built["total"],
         built["coupon_code"], built["payment_method"]),
    )
    order_id = cur.lastrowid

    for item in built["items"]:
        conn.execute(
            "INSERT INTO order_items (order_id, product_id, product_name, price, quantity) VALUES (?, ?, ?, ?, ?)",
            (order_id, item["id"], item["name"], item["price"], item["quantity"]),
        )

    if built["coupon_row"]:
        coupon = built["coupon_row"]
        # Race-condition fix: re-check max_uses atomically at increment time
        # instead of trusting the earlier read-then-write, so two concurrent
        # checkouts can't both squeeze through a coupon's last remaining use.
        if coupon["max_uses"] is not None:
            cur = conn.execute(
                "UPDATE coupons SET used_count = used_count + 1 WHERE id = ? AND used_count < max_uses",
                (coupon["id"],),
            )
            if cur.rowcount == 0:
                raise ValueError("This coupon just reached its usage limit. Please remove it and try again.")
        else:
            conn.execute("UPDATE coupons SET used_count = used_count + 1 WHERE id = ?", (coupon["id"],))

    # CHANGE: for COD, clear the cart now — the order is final. For Razorpay,
    # the cart is kept intact until payment is verified, so a failed/cancelled
    # payment leaves the customer able to retry with the same cart.
    if clear_cart:
        conn.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
    return order_id


# ─────────────────────────────────────────────────────────────────────────
# Phase 4 CHANGE: Order confirmation email
# ─────────────────────────────────────────────────────────────────────────
def claim_confirmation_email(conn, order_id):
    """Atomically flips confirmation_email_sent 0 -> 1 and reports whether
    *this* call won the race. COD sends right after placing; UPI/Card can
    reach 'paid' via the client-side verify() call, the Razorpay webhook, or
    (rarely) both — this guard keeps the customer from getting duplicates."""
    cur = conn.execute(
        "UPDATE orders SET confirmation_email_sent = 1 WHERE id = ? AND confirmation_email_sent = 0",
        (order_id,),
    )
    return cur.rowcount == 1


def send_order_confirmation_if_owed(conn, order_id, user_id):
    """Call after commit. Looks up the customer's email itself so callers
    (COD checkout, Razorpay verify, Razorpay webhook) don't need to."""
    if not claim_confirmation_email(conn, order_id):
        return
    conn.commit()
    user = db.get_user_by_id(conn, user_id)
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    items = db.get_order_items(conn, order_id)
    if user:
        mailer.send_order_confirmation_email(user["email"], db.row_to_order(row), items)
    # This claim_confirmation_email() guard is the one place a new order is
    # guaranteed to be reported exactly once, regardless of which of the
    # three callers (COD checkout, Razorpay verify, Razorpay webhook) got
    # here first — so the admin "new order" alert piggybacks on it too.
    _notify_admin_of_new_order(db.row_to_order(row), items)
    # Same guarantee is exactly what's needed to auto-start the Shiprocket
    # delivery process exactly once per order, whether COD or prepaid.
    _dispatch_to_shiprocket(db.row_to_order(row), items, user)


def _notify_admin_of_new_order(order, items):
    """Best-effort admin alert for a newly-placed/paid order: one row in the
    admin_notifications table (powers the panel's bell icon) plus one email
    to ADMIN_NOTIFY_EMAIL. Either half failing must never break checkout."""
    try:
        notif_conn = db.get_db()
        db.create_notification(
            notif_conn,
            "new_order",
            f"New Order: {order['orderNumber']}",
            f"{order['customerName']} placed an order for ₹{order['total']} "
            f"({'COD' if order['paymentMethod'] == 'cod' else 'Paid Online'}).",
            metadata={"orderNumber": order["orderNumber"], "total": order["total"], "paymentMethod": order["paymentMethod"]},
        )
        notif_conn.close()
    except Exception:
        app.logger.exception("Failed to create new_order notification")

    try:
        mailer.send_admin_new_order_alert_email(app.config.get("ADMIN_NOTIFY_EMAIL"), order, items)
    except Exception:
        app.logger.exception("Failed to send new_order admin alert email")


def _dispatch_to_shiprocket(order, items, user):
    """Best-effort: creates the shipment on Shiprocket and requests pickup
    so the delivery process starts immediately, for both COD and prepaid
    orders. Never blocks or fails checkout if Shiprocket isn't configured
    or is unreachable — it just logs and the order stays shippable
    manually from the Shiprocket dashboard."""
    if not shiprocket_service.SHIPROCKET_ENABLED:
        return
    try:
        ship_conn = db.get_db()
        result = shiprocket_service.create_shipment(
            order, items, ship_conn, customer_email=(user["email"] if user else "")
        )
        if result.get("awb"):
            ship_conn.execute(
                "UPDATE orders SET order_status = 'shipped' WHERE id = ? AND order_status = 'placed'",
                (order["id"],),
            )
            ship_conn.commit()
        ship_conn.close()
    except Exception:
        app.logger.exception("Failed to create Shiprocket shipment for order %s", order.get("orderNumber"))


# ─────────────────────────────────────────────────────────────────────────
# Public product API
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/products", methods=["GET"])
def list_products():
    """
    Supports query params used by the storefront's filter/sort/search bar:
    ?category=bridal&price=under30|30to50|above50&sort=price-low|price-high|newest&q=search
    """
    category = request.args.get("category", "all")
    price_range = request.args.get("price", "all")
    sort_by = request.args.get("sort", "featured")
    query = request.args.get("q", "").strip()

    conn = db.get_db()
    sql = "SELECT * FROM products WHERE is_active = 1"
    params = []

    if category and category != "all":
        sql += " AND category = ?"
        params.append(category)

    if price_range == "under30":
        sql += " AND price < 30000"
    elif price_range == "30to50":
        sql += " AND price BETWEEN 30000 AND 50000"
    elif price_range == "above50":
        sql += " AND price > 50000"

    if query:
        sql += " AND (name LIKE ? OR description LIKE ?)"
        like = f"%{query}%"
        params.extend([like, like])

    if sort_by == "price-low":
        sql += " ORDER BY price ASC"
    elif sort_by == "price-high":
        sql += " ORDER BY price DESC"
    elif sort_by == "newest":
        sql += " ORDER BY created_at DESC"
    else:
        sql += " ORDER BY id ASC"

    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return jsonify([db.row_to_product(r) for r in rows])


@app.route("/api/products/<int:product_id>", methods=["GET"])
def get_product(product_id):
    conn = db.get_db()
    row = conn.execute("SELECT * FROM products WHERE id = ? AND is_active = 1", (product_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "Product not found."}), 404
    return jsonify(db.row_to_product(row))


# ─────────────────────────────────────────────────────────────────────────
# Admin auth API
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return jsonify({"error": "Username and password are required."}), 400

    if rate_limited(login_rate_key("admin:" + username)):
        return jsonify({"error": "Too many login attempts. Please wait a few minutes and try again."}), 429

    conn = db.get_db()
    row = conn.execute("SELECT * FROM admin_users WHERE username = ?", (username,)).fetchone()
    conn.close()

    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "Incorrect username or password."}), 401

    session["is_admin"] = True
    session["admin_username"] = row["username"]
    session.permanent = True
    return jsonify({"success": True, "username": row["username"]})


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("is_admin", None)
    session.pop("admin_username", None)
    return jsonify({"success": True})


@app.route("/api/admin/session", methods=["GET"])
def admin_session():
    return jsonify({"loggedIn": bool(session.get("is_admin")), "username": session.get("admin_username")})


@app.route("/api/admin/change-password", methods=["POST"])
@admin_required
def admin_change_password():
    data = request.get_json(silent=True) or {}
    current_password = data.get("currentPassword") or ""
    new_password = data.get("newPassword") or ""

    if len(new_password) < 8:
        return jsonify({"error": "New password must be at least 8 characters."}), 400

    conn = db.get_db()
    row = conn.execute(
        "SELECT * FROM admin_users WHERE username = ?", (session["admin_username"],)
    ).fetchone()

    if not row or not check_password_hash(row["password_hash"], current_password):
        conn.close()
        return jsonify({"error": "Current password is incorrect."}), 401

    conn.execute(
        "UPDATE admin_users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_password), row["id"]),
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────
# Admin product management API (protected)
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/admin/products", methods=["GET"])
@admin_required
def admin_list_products():
    conn = db.get_db()
    rows = conn.execute("SELECT * FROM products ORDER BY id ASC").fetchall()
    conn.close()
    return jsonify([db.row_to_product(r) for r in rows])


@app.route("/api/admin/products", methods=["POST"])
@admin_required
def admin_create_product():
    data = request.get_json(silent=True) or {}
    cleaned, error = validate_product_payload(data, partial=False)
    if error:
        return jsonify({"error": error}), 400

    cleaned.setdefault("badge", "")
    cleaned.setdefault("emoji", "🌸")
    cleaned.setdefault("fabric", "Pure Banarasi Silk")
    cleaned.setdefault("color", "")
    cleaned.setdefault("stock", 25)
    cleaned.setdefault("image_data", None)
    cleaned.setdefault("images", "[]")

    conn = db.get_db()
    cur = conn.execute(
        """INSERT INTO products
           (name, category, price, badge, emoji, description, image_data, images, fabric, color, stock,
            featured, bestseller, new_arrival)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cleaned["name"], cleaned["category"], cleaned["price"], cleaned["badge"],
            cleaned["emoji"], cleaned["description"], cleaned["image_data"], cleaned["images"], cleaned["fabric"],
            cleaned["color"], cleaned["stock"],
            cleaned.get("featured", 0), cleaned.get("bestseller", 0), cleaned.get("new_arrival", 0),
        ),
    )
    pid = cur.lastrowid
    conn.execute("UPDATE products SET slug = ? WHERE id = ?", (db.slugify(cleaned["name"], pid), pid))
    conn.commit()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    conn.close()
    return jsonify(db.row_to_product(row)), 201


@app.route("/api/admin/products/<int:product_id>", methods=["PUT"])
@admin_required
def admin_update_product(product_id):
    conn = db.get_db()
    existing = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Product not found."}), 404

    data = request.get_json(silent=True) or {}
    cleaned, error = validate_product_payload(data, partial=True)
    if error:
        conn.close()
        return jsonify({"error": error}), 400

    if not cleaned:
        conn.close()
        return jsonify({"error": "No valid fields to update."}), 400

    set_clause = ", ".join(f"{field} = ?" for field in cleaned.keys())
    params = list(cleaned.values()) + [product_id]
    conn.execute(f"UPDATE products SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = ?", params)

    if "name" in cleaned:
        conn.execute("UPDATE products SET slug = ? WHERE id = ?", (db.slugify(cleaned["name"], product_id), product_id))

    conn.commit()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    return jsonify(db.row_to_product(row))


@app.route("/api/admin/products/<int:product_id>", methods=["DELETE"])
@admin_required
def admin_delete_product(product_id):
    conn = db.get_db()
    existing = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Product not found."}), 404

    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/admin/stats", methods=["GET"])
@admin_required
def admin_stats():
    conn = db.get_db()
    total = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    by_category = conn.execute(
        "SELECT category, COUNT(*) AS c FROM products GROUP BY category"
    ).fetchall()
    avg_price = conn.execute("SELECT AVG(price) AS avg FROM products").fetchone()["avg"]
    conn.close()
    return jsonify({
        "total": total,
        "byCategory": {row["category"]: row["c"] for row in by_category},
        "avgPrice": round(avg_price) if avg_price else 0,
    })


# ─────────────────────────────────────────────────────────────────────────
# Admin Notifications — powers the bell icon in the admin panel. Fed by
# customer_login (and can be extended the same way for customer_signup,
# new_order, etc. — see database.create_notification).
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/admin/notifications", methods=["GET"])
@admin_required
def admin_notifications():
    unread_only = request.args.get("unread") == "true"
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except ValueError:
        limit = 50
    conn = db.get_db()
    notifications = db.get_notifications(conn, unread_only=unread_only, limit=limit)
    unread_count = db.get_unread_notification_count(conn)
    conn.close()
    return jsonify({"notifications": notifications, "unreadCount": unread_count})


@app.route("/api/admin/notifications/unread-count", methods=["GET"])
@admin_required
def admin_notifications_unread_count():
    conn = db.get_db()
    count = db.get_unread_notification_count(conn)
    conn.close()
    return jsonify({"unreadCount": count})


@app.route("/api/admin/notifications/<int:notif_id>/read", methods=["POST"])
@admin_required
def admin_notification_mark_read(notif_id):
    conn = db.get_db()
    db.mark_notification_read(conn, notif_id)
    conn.close()
    return jsonify({"success": True})


@app.route("/api/admin/notifications/read-all", methods=["POST"])
@admin_required
def admin_notifications_mark_all_read():
    conn = db.get_db()
    db.mark_all_notifications_read(conn)
    conn.close()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: Customer auth API (with Stateless Signed OTP Tokens for Vercel)
# ─────────────────────────────────────────────────────────────────────────
OTP_SALT = "jeevani-otp-v1"
SIGNUP_SALT = "jeevani-signup-v1"


def _hash_otp(otp_code):
    """Secure SHA-256 hash of the 6-digit OTP code."""
    return hashlib.sha256(str(otp_code).strip().encode("utf-8")).hexdigest()


def _get_serializer():
    return URLSafeTimedSerializer(app.config["SECRET_KEY"])


def _issue_stateless_login_otp(row):
    """Generates a fresh 6-digit code, saves to DB if possible (for local/persistent servers),
    and generates a cryptographically signed HMAC token for serverless (Vercel) environments."""
    otp_code = f"{secrets.randbelow(1000000):06d}"
    user_id = row["id"]
    email = (row["email"] or "").strip() if ("email" in row.keys() and row["email"]) else ""
    name = (row["name"] or "").strip() if ("name" in row.keys() and row["name"]) else "Valued Patron"

    try:
        conn = db.get_db()
        db.set_login_otp(conn, user_id, otp_code)
        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning("Could not set login OTP in DB: %s", e)

    payload = {
        "user_id": user_id,
        "identifier": email or str(user_id),
        "otp_hash": _hash_otp(otp_code),
        "created_at": time.time(),
        "type": "login",
    }
    s = _get_serializer()
    token = s.dumps(payload, salt=OTP_SALT)
    if email:
        mailer.send_login_otp_email(email, name, otp_code, db.LOGIN_OTP_TTL_MINUTES)
    return token


def _verify_stateless_login_otp(token, submitted_code):
    """Verifies signed login OTP token without relying on serverless DB storage."""
    if not token or not submitted_code:
        return "invalid", None
    s = _get_serializer()
    try:
        payload = s.loads(token, salt=OTP_SALT, max_age=db.LOGIN_OTP_TTL_MINUTES * 60 + 30)
    except SignatureExpired:
        return "expired", None
    except BadSignature:
        return "invalid", None
    except Exception:
        return "invalid", None

    expected_hash = payload.get("otp_hash")
    submitted_hash = _hash_otp(submitted_code)
    if not expected_hash or not hmac.compare_digest(expected_hash, submitted_hash):
        return "invalid", None

    return "ok", payload


def _issue_stateless_signup_otp(user_data, otp_code=None):
    """Generates a signed token containing signup credentials and OTP hash so
    the account can be safely activated even across different serverless instances."""
    if not otp_code:
        otp_code = f"{secrets.randbelow(1000000):06d}"

    payload = {
        "name": user_data["name"],
        "email": user_data["email"],
        "phone": user_data["phone"],
        "password_hash": user_data["password_hash"],
        "otp_hash": _hash_otp(otp_code),
        "created_at": time.time(),
        "type": "signup",
    }
    s = _get_serializer()
    token = s.dumps(payload, salt=SIGNUP_SALT)
    mailer.send_login_otp_email(user_data["email"], user_data["name"], otp_code, db.LOGIN_OTP_TTL_MINUTES)
    return token


def _verify_stateless_signup_otp(token, submitted_code):
    """Verifies signed signup OTP token without relying on serverless DB storage."""
    if not token or not submitted_code:
        return "invalid", None
    s = _get_serializer()
    try:
        payload = s.loads(token, salt=SIGNUP_SALT, max_age=db.LOGIN_OTP_TTL_MINUTES * 60 + 30)
    except SignatureExpired:
        return "expired", None
    except BadSignature:
        return "invalid", None
    except Exception:
        return "invalid", None

    expected_hash = payload.get("otp_hash")
    submitted_hash = _hash_otp(submitted_code)
    if not expected_hash or not hmac.compare_digest(expected_hash, submitted_hash):
        return "invalid", None

    return "ok", payload


@app.route("/api/auth/signup", methods=["POST"])
def auth_signup():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()
    password = data.get("password") or ""

    if not name or not email or not phone or not password:
        return jsonify({"error": "Please fill in all fields to create your account."}), 400
    if len(name) > 200:
        return jsonify({"error": "Name is too long."}), 400
    if not db.EMAIL_RE.match(email):
        return jsonify({"error": "Please enter a valid email address."}), 400
    if len(password) < 6:
        return jsonify({"error": "Please choose a password that is at least 6 characters long."}), 400

    conn = db.get_db()
    if db.email_exists(conn, email):
        conn.close()
        return jsonify({"error": "An account with this email already exists. Please sign in instead."}), 409

    pwd_hash = generate_password_hash(password)
    # Attempt local DB insert (persists on local / VPS / Render)
    try:
        conn.execute(
            "INSERT INTO users (name, email, phone, password_hash) VALUES (?, ?, ?, ?)",
            (name, email, phone, pwd_hash),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "An account with this email already exists. Please sign in instead."}), 409
    except Exception as e:
        app.logger.warning("DB insert on signup (will finalize on OTP verify if needed): %s", e)
    finally:
        conn.close()

    user_data = {
        "name": name,
        "email": email,
        "phone": phone,
        "password_hash": pwd_hash,
    }
    otp_token = _issue_stateless_signup_otp(user_data)

    return jsonify({
        "otpRequired": True,
        "otpToken": otp_token,
        "email": email,
        "emailHint": _mask_email(email),
        "message": f"We've sent a 6-digit code to {_mask_email(email)}. Enter it below to activate your account.",
    }), 201


@app.route("/api/auth/signup/verify-otp", methods=["POST"])
def auth_signup_verify_otp():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    otp = (data.get("otp") or "").strip()
    otp_token = (data.get("otpToken") or "").strip()

    if not email or not otp:
        return jsonify({"error": "Please enter the 6-digit code."}), 400

    # Same rate limiting as login OTP verification
    if rate_limited(login_rate_key("signup-otp:" + email)):
        return jsonify({"error": "Too many attempts. Please wait a few minutes and try again."}), 429

    stateless_ok = False
    signup_payload = None
    if otp_token:
        res, payload = _verify_stateless_signup_otp(otp_token, otp)
        if res == "expired":
            return jsonify({"error": "This code has expired. Please sign up again."}), 400
        elif res == "ok" and payload and payload.get("email", "").lower() == email:
            stateless_ok = True
            signup_payload = payload

    conn = db.get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    # If DB in this serverless container doesn't have the user yet, create it from verified signed payload
    if not row and stateless_ok and signup_payload:
        try:
            cur = conn.execute(
                "INSERT INTO users (name, email, phone, password_hash) VALUES (?, ?, ?, ?)",
                (signup_payload["name"], signup_payload["email"], signup_payload["phone"], signup_payload["password_hash"]),
            )
            conn.commit()
            row = db.get_user_by_id(conn, cur.lastrowid)
        except Exception:
            app.logger.exception("Failed to insert user on stateless OTP verify")
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    if not stateless_ok:
        # Fallback to DB check (for local development or when token wasn't provided)
        if not row:
            conn.close()
            return jsonify({"error": "That code is incorrect. Please check and try again."}), 401
        result = db.verify_login_otp(conn, row["id"], otp)
        conn.commit()
        if result == "expired":
            conn.close()
            return jsonify({"error": "This code has expired. Please sign up again."}), 400
        if result == "locked":
            conn.close()
            return jsonify({"error": "Too many incorrect attempts. Please sign up again."}), 429
        if result != "ok":
            conn.close()
            return jsonify({"error": "That code is incorrect. Please check and try again."}), 401

    if row:
        try:
            db.clear_login_otp(conn, row["id"])
            conn.commit()
        except Exception:
            pass
        session["user_id"] = row["id"]
        session.permanent = True
        user_dict = db.row_to_user(row)
        conn.close()
        return jsonify({"success": True, "user": user_dict})

    conn.close()
    return jsonify({"error": "Failed to activate account. Please sign up again."}), 400


@app.route("/api/auth/signup/resend-otp", methods=["POST"])
def auth_signup_resend_otp():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    otp_token = (data.get("otpToken") or "").strip()

    if not email:
        return jsonify({"error": "Please sign up again."}), 400

    if rate_limited(login_rate_key("signup-otp-resend:" + email)):
        return jsonify({"error": "Please wait a little before requesting another code."}), 429

    new_token = None
    if otp_token:
        s = _get_serializer()
        try:
            payload = s.loads(otp_token, salt=SIGNUP_SALT, max_age=1800)
            if payload and payload.get("email", "").lower() == email:
                new_token = _issue_stateless_signup_otp(payload)
        except Exception:
            pass

    if not new_token:
        conn = db.get_db()
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()
        if row and not row["is_blocked"] and row["email"]:
            new_token = _issue_login_otp(row)

    return jsonify({"success": True, "otpToken": new_token, "message": "If that account exists, a new code has been sent."})


def _mask_email(email):
    """al***@gmail.com — enough for the customer to recognise their own
    inbox on the 'enter the code we sent' screen without fully re-exposing
    the address to anyone watching the response."""
    try:
        local, domain = email.split("@", 1)
    except ValueError:
        return email
    if len(local) <= 2:
        masked = local[0] + "*" * max(len(local) - 1, 1)
    else:
        masked = local[:2] + "*" * (len(local) - 2)
    return f"{masked}@{domain}"


def _issue_login_otp(row):
    """Generates a fresh 6-digit code, stores it against this user, and
    emails it. Returns the signed token for stateless serverless verification."""
    return _issue_stateless_login_otp(row)


def _notify_admin_of_login(row):
    """Best-effort admin alert for a completed login: one row in the
    admin_notifications table (powers the panel's bell icon) plus one email
    to ADMIN_NOTIFY_EMAIL. Either half failing must never block or fail the
    customer's own login."""
    try:
        notif_conn = db.get_db()
        db.create_notification(
            notif_conn,
            "customer_login",
            f"Customer Login: {row['name']}",
            f"{row['name']} ({row['email']}) just logged in.",
            user_id=row["id"],
            metadata={"email": row["email"], "phone": row["phone"], "ip": request.remote_addr},
        )
        notif_conn.close()
    except Exception:
        app.logger.exception("Failed to create customer_login notification")

    try:
        mailer.send_admin_login_alert_email(
            app.config.get("ADMIN_NOTIFY_EMAIL"), db.row_to_user(row), ip_address=request.remote_addr
        )
    except Exception:
        app.logger.exception("Failed to send customer_login admin alert email")


@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip()
    password = data.get("password") or ""

    if not identifier or not password:
        return jsonify({"error": "Please enter your email/phone and password."}), 400

    if rate_limited(login_rate_key("customer:" + identifier)):
        return jsonify({"error": "Too many login attempts. Please wait a few minutes and try again."}), 429

    conn = db.get_db()
    row = db.get_user_by_identifier(conn, identifier)
    conn.close()

    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "We could not find an account matching those details. Please check and try again, or create a new account."}), 401

    if row["is_blocked"]:
        return jsonify({"error": "This account has been suspended. Please contact support for assistance."}), 403

    if not row["email"]:
        session["user_id"] = row["id"]
        session.permanent = True
        _notify_admin_of_login(row)
        return jsonify({"success": True, "user": db.row_to_user(row)})

    otp_token = _issue_login_otp(row)
    return jsonify({
        "otpRequired": True,
        "otpToken": otp_token,
        "identifier": identifier,
        "emailHint": _mask_email(row["email"]),
        "message": f"We've sent a 6-digit code to {_mask_email(row['email'])}. Enter it below to finish signing in.",
    })


@app.route("/api/auth/login/verify-otp", methods=["POST"])
def auth_login_verify_otp():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip()
    otp = (data.get("otp") or "").strip()
    otp_token = (data.get("otpToken") or "").strip()

    if not identifier or not otp:
        return jsonify({"error": "Please enter the 6-digit code."}), 400

    if rate_limited(login_rate_key("otp:" + identifier)):
        return jsonify({"error": "Too many attempts. Please wait a few minutes and try again."}), 429

    stateless_ok = False
    token_user_id = None
    if otp_token:
        res, payload = _verify_stateless_login_otp(otp_token, otp)
        if res == "expired":
            return jsonify({"error": "This code has expired. Please request a new one."}), 400
        elif res == "ok" and payload:
            stateless_ok = True
            token_user_id = payload.get("user_id")

    conn = db.get_db()
    row = db.get_user_by_identifier(conn, identifier)
    if not row and token_user_id:
        row = db.get_user_by_id(conn, token_user_id)

    if not row:
        conn.close()
        return jsonify({"error": "Session expired. Please sign in again."}), 400

    if row["is_blocked"]:
        conn.close()
        return jsonify({"error": "This account has been suspended. Please contact support for assistance."}), 403

    if not stateless_ok:
        # Fallback to DB check
        result = db.verify_login_otp(conn, row["id"], otp)
        conn.commit()
        if result == "expired":
            conn.close()
            return jsonify({"error": "This code has expired. Please request a new one."}), 400
        if result == "locked":
            conn.close()
            return jsonify({"error": "Too many incorrect attempts. Please request a new code."}), 429
        if result != "ok":
            conn.close()
            return jsonify({"error": "That code is incorrect. Please check and try again."}), 401

    try:
        db.clear_login_otp(conn, row["id"])
        conn.commit()
    except Exception:
        pass
    conn.close()

    session["user_id"] = row["id"]
    session.permanent = True

    _notify_admin_of_login(row)

    return jsonify({"success": True, "user": db.row_to_user(row)})


@app.route("/api/auth/login/resend-otp", methods=["POST"])
def auth_login_resend_otp():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("identifier") or "").strip()

    if not identifier:
        return jsonify({"error": "Please sign in again."}), 400

    if rate_limited(login_rate_key("otp-resend:" + identifier)):
        return jsonify({"error": "Please wait a little before requesting another code."}), 429

    conn = db.get_db()
    row = db.get_user_by_identifier(conn, identifier)
    conn.close()

    otp_token = None
    if row and not row["is_blocked"] and row["email"]:
        otp_token = _issue_login_otp(row)

    return jsonify({"success": True, "otpToken": otp_token, "message": "If that account exists, a new code has been sent."})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    session.pop("user_id", None)
    return jsonify({"success": True})


@app.route("/api/auth/session", methods=["GET"])
def auth_session():
    user_id = session.get("user_id")
    if not user_id:
        return jsonify({"loggedIn": False, "user": None})

    conn = db.get_db()
    row = db.get_user_by_id(conn, user_id)
    conn.close()

    if not row:
        session.pop("user_id", None)
        return jsonify({"loggedIn": False, "user": None})

    # Phase 6 CHANGE: if an admin blocks this account while the customer is
    # mid-session, end the session on their next request rather than letting
    # an already-issued cookie keep working until it expires.
    if row["is_blocked"]:
        session.pop("user_id", None)
        return jsonify({"loggedIn": False, "user": None})

    return jsonify({"loggedIn": True, "user": db.row_to_user(row)})


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: My Profile — update details / change password
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/auth/profile", methods=["PUT"])
@customer_required
def update_profile():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()

    if not name or not phone:
        return jsonify({"error": "Name and phone are required."}), 400
    if len(name) > 200:
        return jsonify({"error": "Name is too long."}), 400

    conn = db.get_db()
    conn.execute("UPDATE users SET name = ?, phone = ? WHERE id = ?", (name, phone, session["user_id"]))
    conn.commit()
    row = db.get_user_by_id(conn, session["user_id"])
    conn.close()
    return jsonify({"success": True, "user": db.row_to_user(row)})


@app.route("/api/auth/change-password", methods=["POST"])
@customer_required
def change_customer_password():
    data = request.get_json(silent=True) or {}
    current_password = data.get("currentPassword") or ""
    new_password = data.get("newPassword") or ""

    if len(new_password) < 6:
        return jsonify({"error": "Please choose a new password that is at least 6 characters long."}), 400

    conn = db.get_db()
    row = db.get_user_by_id(conn, session["user_id"])
    if not row or not check_password_hash(row["password_hash"], current_password):
        conn.close()
        return jsonify({"error": "Current password is incorrect."}), 401

    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), session["user_id"]))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────
# Phase 4 CHANGE: Forgot / reset password (email-based, via SMTP)
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/auth/forgot-password", methods=["POST"])
def forgot_password():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if not email or not db.EMAIL_RE.match(email):
        return jsonify({"error": "Please enter a valid email address."}), 400

    # Security fix: rate-limit, same pattern as the login endpoints, so this
    # can't be hammered to spam a mailbox or probe which emails have accounts.
    if rate_limited(login_rate_key("reset:" + email)):
        return jsonify({"error": "Too many requests. Please wait a few minutes and try again."}), 429

    conn = db.get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if row:
        token = secrets.token_urlsafe(32)
        db.set_reset_token(conn, row["id"], token)
        conn.commit()
        reset_url = f"{app.config['SITE_URL'].rstrip('/')}/saree.html?resetToken={token}"
        mailer.send_password_reset_email(row["email"], row["name"], reset_url, db.RESET_TOKEN_TTL_MINUTES)
    conn.close()

    # Security fix: identical response whether or not the email has an
    # account, so this endpoint can't be used to enumerate registered users.
    return jsonify({
        "success": True,
        "message": "If an account exists with that email, a password reset link has been sent.",
    })


@app.route("/api/auth/reset-password", methods=["POST"])
def reset_password():
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()
    new_password = data.get("newPassword") or ""

    if not token:
        return jsonify({"error": "Missing or invalid reset link."}), 400
    if len(new_password) < 6:
        return jsonify({"error": "Please choose a password that is at least 6 characters long."}), 400

    conn = db.get_db()
    row = db.get_user_by_reset_token(conn, token)
    if not row:
        conn.close()
        return jsonify({"error": "This reset link is invalid or has expired. Please request a new one."}), 400

    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), row["id"]))
    db.clear_reset_token(conn, row["id"])
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: Cart API (server-persisted per logged-in customer)
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/cart", methods=["GET"])
@customer_required
def get_cart():
    conn = db.get_db()
    items = db.get_cart_items(conn, session["user_id"])
    conn.close()
    return jsonify(items)


@app.route("/api/cart", methods=["POST"])
@customer_required
def add_to_cart():
    data = request.get_json(silent=True) or {}
    try:
        product_id = int(data.get("productId"))
        quantity = int(data.get("quantity", 1))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid product or quantity."}), 400

    if quantity < 1:
        return jsonify({"error": "Quantity must be at least 1."}), 400

    conn = db.get_db()
    product = conn.execute("SELECT id FROM products WHERE id = ? AND is_active = 1", (product_id,)).fetchone()
    if not product:
        conn.close()
        return jsonify({"error": "Product not found."}), 404

    existing = conn.execute(
        "SELECT quantity FROM cart_items WHERE user_id = ? AND product_id = ?",
        (session["user_id"], product_id),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE cart_items SET quantity = ? WHERE user_id = ? AND product_id = ?",
            (existing["quantity"] + quantity, session["user_id"], product_id),
        )
    else:
        conn.execute(
            "INSERT INTO cart_items (user_id, product_id, quantity) VALUES (?, ?, ?)",
            (session["user_id"], product_id, quantity),
        )
    conn.commit()
    items = db.get_cart_items(conn, session["user_id"])
    conn.close()
    return jsonify(items), 201


@app.route("/api/cart/<int:product_id>", methods=["PUT"])
@customer_required
def update_cart_item(product_id):
    data = request.get_json(silent=True) or {}
    try:
        quantity = int(data.get("quantity"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid quantity."}), 400

    conn = db.get_db()
    if quantity < 1:
        conn.execute(
            "DELETE FROM cart_items WHERE user_id = ? AND product_id = ?",
            (session["user_id"], product_id),
        )
    else:
        existing = conn.execute(
            "SELECT id FROM cart_items WHERE user_id = ? AND product_id = ?",
            (session["user_id"], product_id),
        ).fetchone()
        if not existing:
            conn.close()
            return jsonify({"error": "Item not in cart."}), 404
        conn.execute(
            "UPDATE cart_items SET quantity = ? WHERE user_id = ? AND product_id = ?",
            (quantity, session["user_id"], product_id),
        )
    conn.commit()
    items = db.get_cart_items(conn, session["user_id"])
    conn.close()
    return jsonify(items)


@app.route("/api/cart/<int:product_id>", methods=["DELETE"])
@customer_required
def remove_cart_item(product_id):
    conn = db.get_db()
    conn.execute(
        "DELETE FROM cart_items WHERE user_id = ? AND product_id = ?",
        (session["user_id"], product_id),
    )
    conn.commit()
    items = db.get_cart_items(conn, session["user_id"])
    conn.close()
    return jsonify(items)


@app.route("/api/cart/clear", methods=["POST"])
@customer_required
def clear_cart():
    conn = db.get_db()
    conn.execute("DELETE FROM cart_items WHERE user_id = ?", (session["user_id"],))
    conn.commit()
    conn.close()
    return jsonify([])


@app.route("/api/cart/merge", methods=["POST"])
@customer_required
def merge_cart():
    """Phase 2 CHANGE: called right after login/signup to fold a guest's
    in-browser cart into their account cart, so nothing is lost on sign-in."""
    data = request.get_json(silent=True) or {}
    raw_items = data.get("items") or []

    conn = db.get_db()
    for item in raw_items:
        try:
            product_id = int(item.get("productId") if isinstance(item, dict) else item.get("id"))
            quantity = int(item.get("quantity", 1))
        except (TypeError, ValueError, AttributeError):
            continue
        if quantity < 1:
            continue

        product = conn.execute("SELECT id FROM products WHERE id = ? AND is_active = 1", (product_id,)).fetchone()
        if not product:
            continue

        existing = conn.execute(
            "SELECT quantity FROM cart_items WHERE user_id = ? AND product_id = ?",
            (session["user_id"], product_id),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE cart_items SET quantity = ? WHERE user_id = ? AND product_id = ?",
                (existing["quantity"] + quantity, session["user_id"], product_id),
            )
        else:
            conn.execute(
                "INSERT INTO cart_items (user_id, product_id, quantity) VALUES (?, ?, ?)",
                (session["user_id"], product_id, quantity),
            )
    conn.commit()
    items = db.get_cart_items(conn, session["user_id"])
    conn.close()
    return jsonify(items)


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: Wishlist API (server-persisted per logged-in customer)
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/wishlist", methods=["GET"])
@customer_required
def get_wishlist():
    conn = db.get_db()
    items = db.get_wishlist_items(conn, session["user_id"])
    conn.close()
    return jsonify(items)


@app.route("/api/wishlist/<int:product_id>", methods=["POST"])
@customer_required
def add_to_wishlist(product_id):
    conn = db.get_db()
    product = conn.execute("SELECT id FROM products WHERE id = ? AND is_active = 1", (product_id,)).fetchone()
    if not product:
        conn.close()
        return jsonify({"error": "Product not found."}), 404

    try:
        conn.execute(
            "INSERT INTO wishlist (user_id, product_id) VALUES (?, ?)",
            (session["user_id"], product_id),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # already wishlisted — idempotent

    items = db.get_wishlist_items(conn, session["user_id"])
    conn.close()
    return jsonify(items), 201


@app.route("/api/wishlist/<int:product_id>", methods=["DELETE"])
@customer_required
def remove_from_wishlist(product_id):
    conn = db.get_db()
    conn.execute(
        "DELETE FROM wishlist WHERE user_id = ? AND product_id = ?",
        (session["user_id"], product_id),
    )
    conn.commit()
    items = db.get_wishlist_items(conn, session["user_id"])
    conn.close()
    return jsonify(items)


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: Coupon validation (customer-facing)
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/coupons/validate", methods=["POST"])
@customer_required
def validate_coupon_route():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    if not code:
        return jsonify({"error": "Please enter a coupon code."}), 400

    conn = db.get_db()
    items = db.get_cart_items(conn, session["user_id"])
    subtotal = sum(i["price"] * i["quantity"] for i in items)

    coupon = db.find_active_coupon(conn, code)
    if not coupon:
        conn.close()
        return jsonify({"error": "Invalid or expired coupon code."}), 404

    discount, error = db.compute_coupon_discount(coupon, subtotal)
    conn.close()
    if error:
        return jsonify({"error": error}), 400

    return jsonify({
        "valid": True,
        "code": coupon["code"],
        "discountType": coupon["discount_type"],
        "discountValue": coupon["discount_value"],
        "discount": discount,
        "subtotal": subtotal,
    })


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: Orders — Cash on Delivery
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/orders", methods=["POST"])
@customer_required
def create_order():
    data = request.get_json(silent=True) or {}
    conn = db.get_db()

    try:
        built = build_order_from_cart(conn, session["user_id"], data)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

    order_number = db.generate_order_number()
    try:
        order_id = persist_order(conn, session["user_id"], order_number, built)
        conn.commit()
    except ValueError as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 409

    # Phase 4 CHANGE: COD orders are final the moment they're placed (no
    # separate payment-confirmation step), so email right away.
    send_order_confirmation_if_owed(conn, order_id, session["user_id"])

    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    items = db.get_order_items(conn, order_id)
    conn.close()
    return jsonify(db.row_to_order(row, items)), 201


@app.route("/api/orders", methods=["GET"])
@customer_required
def list_orders():
    conn = db.get_db()
    orders = db.get_orders_for_user(conn, session["user_id"])
    conn.close()
    return jsonify(orders)


@app.route("/api/orders/<order_number>", methods=["GET"])
@customer_required
def get_order(order_number):
    conn = db.get_db()
    row = db.get_order_by_number(conn, order_number, session["user_id"])
    if not row:
        conn.close()
        return jsonify({"error": "Order not found."}), 404
    items = db.get_order_items(conn, row["id"])
    conn.close()
    return jsonify(db.row_to_order(row, items))


# ─────────────────────────────────────────────────────────────────────────
# CHANGE: Customer-raised Return / Replacement requests.
# Policy: one combined replace-or-return allowance per delivered order
# (orders.replacement_used). Deliberately generic error messages below —
# the one-time limit itself is never spelled out to the customer; the
# "Request Replacement/Return" option simply stops appearing once used.
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/orders/<order_number>/return-request", methods=["POST"])
@customer_required
def create_return_request(order_number):
    data = request.get_json(silent=True) or {}
    req_type = (data.get("type") or "").strip().lower()
    reason = (data.get("reason") or "").strip()[:500]

    if req_type not in VALID_RETURN_REQUEST_TYPES:
        return jsonify({"error": "Please choose Replace or Return."}), 400

    conn = db.get_db()
    row = db.get_order_by_number(conn, order_number, session["user_id"])
    if not row:
        conn.close()
        return jsonify({"error": "Order not found."}), 404

    if row["order_status"] != "delivered" or row["replacement_used"]:
        conn.close()
        return jsonify({"error": "Return/Replacement is not available for this order."}), 400

    conn.execute(
        "INSERT INTO return_requests (order_id, user_id, type, reason) VALUES (?, ?, ?, ?)",
        (row["id"], session["user_id"], req_type, reason),
    )
    conn.execute("UPDATE orders SET replacement_used = 1 WHERE id = ?", (row["id"],))
    conn.commit()

    try:
        db.create_notification(
            conn,
            "return_request",
            f"{'Replacement' if req_type == 'replace' else 'Return'} Requested: {order_number}",
            f"{row['customer_name']} requested a {req_type} for order #{order_number}."
            + (f" Reason: {reason}" if reason else ""),
            user_id=session["user_id"],
            metadata={"orderNumber": order_number, "type": req_type},
        )
    except Exception:
        app.logger.exception("Failed to create return_request notification")

    updated = conn.execute("SELECT * FROM orders WHERE id = ?", (row["id"],)).fetchone()
    items = db.get_order_items(conn, row["id"])
    conn.close()
    return jsonify(db.row_to_order(updated, items)), 201


# ─────────────────────────────────────────────────────────────────────────
# CHANGE: Customer-initiated order cancellation. Only allowed while the
# order is still "placed" (i.e. before the shop has shipped it) — once it's
# shipped, cancellation has to go through support/admin since the parcel is
# already in the courier's hands. Cancelling here restocks the items (same
# as the admin-side cancel) and, if it was prepaid, just flags the order so
# admin knows a manual refund is owed — this project has no automated
# Razorpay refund flow yet.
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/orders/<order_number>/cancel", methods=["POST"])
@customer_required
def cancel_order(order_number):
    conn = db.get_db()
    row = db.get_order_by_number(conn, order_number, session["user_id"])
    if not row:
        conn.close()
        return jsonify({"error": "Order not found."}), 404

    if row["order_status"] != "placed":
        conn.close()
        return jsonify({"error": "This order can no longer be cancelled. Please contact support."}), 400

    for item in db.get_order_items(conn, row["id"]):
        conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (item["quantity"], item["productId"]))

    conn.execute("UPDATE orders SET order_status = 'cancelled' WHERE id = ?", (row["id"],))
    conn.commit()

    needs_refund = row["payment_method"] != "cod" and row["payment_status"] == "paid"
    try:
        db.create_notification(
            conn,
            "order_cancelled",
            f"Order Cancelled by Customer: {order_number}",
            f"{row['customer_name']} cancelled order #{order_number}."
            + (" Payment was already captured — refund needs to be issued manually." if needs_refund else ""),
            user_id=session["user_id"],
            metadata={"orderNumber": order_number, "needsRefund": needs_refund},
        )
    except Exception:
        app.logger.exception("Failed to create order_cancelled notification")

    updated = conn.execute("SELECT * FROM orders WHERE id = ?", (row["id"],)).fetchone()
    items = db.get_order_items(conn, row["id"])
    conn.close()
    return jsonify(db.row_to_order(updated, items))


# ─────────────────────────────────────────────────────────────────────────
# Phase 3 CHANGE: Razorpay checkout (UPI / Card)
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/orders/razorpay/create", methods=["POST"])
@customer_required
def razorpay_create_order():
    if not RAZORPAY_ENABLED:
        return jsonify({"error": "Online payments aren't configured yet. Please choose Cash on Delivery."}), 503

    data = request.get_json(silent=True) or {}
    conn = db.get_db()

    # Reliability/security fix: release any of this customer's Razorpay
    # orders that were created but never completed and have gone stale, so
    # abandoned checkouts don't permanently (or repeatedly, via direct API
    # calls bypassing the disabled "Place Order" button) lock up stock.
    sweep_stale_pending_orders(conn, user_id=session["user_id"])

    try:
        built = build_order_from_cart(conn, session["user_id"], data)
    except ValueError as e:
        conn.close()
        return jsonify({"error": str(e)}), 400

    order_number = db.generate_order_number()
    try:
        order_id = persist_order(conn, session["user_id"], order_number, built, clear_cart=False)
        conn.commit()
    except ValueError as e:
        conn.rollback()
        conn.close()
        return jsonify({"error": str(e)}), 409

    # Create the mirrored order on Razorpay's side. If this fails, undo the
    # local order (restock + re-credit the coupon) so nothing is left dangling.
    try:
        rp_res = requests.post(
            "https://api.razorpay.com/v1/orders",
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            json={"amount": built["total"] * 100, "currency": "INR", "receipt": order_number},
            timeout=10,
        )
        rp_res.raise_for_status()
        rp_order = rp_res.json()
    except requests.RequestException:
        fresh_row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        release_order(conn, fresh_row)
        conn.commit()
        conn.close()
        return jsonify({"error": "Could not start the payment. Please try again."}), 502

    conn.execute("UPDATE orders SET razorpay_order_id = ? WHERE id = ?", (rp_order["id"], order_id))
    conn.commit()
    conn.close()

    return jsonify({
        "orderNumber": order_number,
        "razorpayOrderId": rp_order["id"],
        "amount": rp_order["amount"],
        "currency": rp_order["currency"],
        "keyId": RAZORPAY_KEY_ID,
    }), 201


@app.route("/api/orders/razorpay/verify", methods=["POST"])
@customer_required
def razorpay_verify():
    if not RAZORPAY_ENABLED:
        return jsonify({"error": "Online payments aren't configured."}), 503

    data = request.get_json(silent=True) or {}
    order_number = data.get("orderNumber") or ""
    razorpay_order_id = data.get("razorpayOrderId") or ""
    razorpay_payment_id = data.get("razorpayPaymentId") or ""
    razorpay_signature = data.get("razorpaySignature") or ""

    if not all([order_number, razorpay_order_id, razorpay_payment_id, razorpay_signature]):
        return jsonify({"error": "Missing payment verification details."}), 400

    expected_signature = hmac.new(
        RAZORPAY_KEY_SECRET.encode(), f"{razorpay_order_id}|{razorpay_payment_id}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, razorpay_signature):
        return jsonify({"error": "Payment verification failed."}), 400

    conn = db.get_db()
    row = db.get_order_by_number(conn, order_number, session["user_id"])
    if not row or row["razorpay_order_id"] != razorpay_order_id:
        conn.close()
        return jsonify({"error": "Order not found."}), 404

    conn.execute(
        "UPDATE orders SET payment_status = 'paid', razorpay_payment_id = ? WHERE id = ?",
        (razorpay_payment_id, row["id"]),
    )
    conn.commit()

    # Phase 4 CHANGE: payment is confirmed — send the order confirmation now.
    send_order_confirmation_if_owed(conn, row["id"], session["user_id"])

    updated = conn.execute("SELECT * FROM orders WHERE id = ?", (row["id"],)).fetchone()
    items = db.get_order_items(conn, row["id"])
    conn.close()
    return jsonify(db.row_to_order(updated, items))


@app.route("/api/orders/razorpay/cancel", methods=["POST"])
@customer_required
def razorpay_cancel():
    """Called when the customer dismisses the Razorpay widget without
    completing payment, so stock/coupon usage don't stay locked forever."""
    data = request.get_json(silent=True) or {}
    order_number = data.get("orderNumber") or ""

    conn = db.get_db()
    row = db.get_order_by_number(conn, order_number, session["user_id"])
    if not row or row["payment_status"] != "pending":
        conn.close()
        return jsonify({"success": True})

    # Phase 3 fix: restores stock + cart, not just stock, so "Try Again" on
    # the payment-failed page has the customer's items to check out again.
    release_order(conn, row)
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────
# Security/reliability fix: Razorpay webhook.
# The client-side `handler` callback in razorpay.js is the *only* thing that
# previously marked an order paid — if the customer's browser crashes, loses
# network, or the tab is closed right after a successful payment, the money
# is captured but the order stays 'pending' forever. A webhook, configured
# server-to-server in the Razorpay dashboard, is the reliable source of
# truth and works even if the customer never returns to the site.
#
# Setup: in the Razorpay dashboard, add a webhook pointing at
# POST /api/webhooks/razorpay, subscribed to "payment.captured" (and
# optionally "payment.failed"), and set RAZORPAY_WEBHOOK_SECRET to the
# secret shown there.
# ─────────────────────────────────────────────────────────────────────────
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")


@app.route("/api/webhooks/razorpay", methods=["POST"])
def razorpay_webhook():
    if not RAZORPAY_WEBHOOK_SECRET:
        # Not configured — the client-side verify() flow still works as a
        # fallback, but abandoned/never-returned-to payments won't self-heal.
        return jsonify({"error": "Webhook not configured."}), 503

    raw_body = request.get_data()
    signature = request.headers.get("X-Razorpay-Signature", "")
    expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        return jsonify({"error": "Invalid signature."}), 400

    payload = request.get_json(silent=True) or {}
    event = payload.get("event", "")
    payment_entity = (
        payload.get("payload", {}).get("payment", {}).get("entity", {})
    )
    razorpay_order_id = payment_entity.get("order_id", "")
    razorpay_payment_id = payment_entity.get("id", "")
    if not razorpay_order_id:
        return jsonify({"success": True})  # nothing actionable, ack anyway

    conn = db.get_db()
    row = conn.execute("SELECT * FROM orders WHERE razorpay_order_id = ?", (razorpay_order_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"success": True})

    if event == "payment.captured" and row["payment_status"] != "paid":
        conn.execute(
            "UPDATE orders SET payment_status = 'paid', razorpay_payment_id = ? WHERE id = ?",
            (razorpay_payment_id, row["id"]),
        )
        # Payment is confirmed paid — the cart may still hold these items if
        # the customer's browser never got to onOrderPlaced(); clear it now.
        conn.execute("DELETE FROM cart_items WHERE user_id = ?", (row["user_id"],))
        conn.commit()
        # Phase 4 CHANGE: this is the reliable path for a customer who never
        # returned to the browser after paying — claim_confirmation_email's
        # atomic guard means this won't duplicate an email already sent by
        # the client-side verify() call.
        send_order_confirmation_if_owed(conn, row["id"], row["user_id"])
    elif event == "payment.failed" and row["payment_status"] == "pending":
        release_order(conn, row)
        conn.commit()

    conn.close()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: Reviews
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/products/<int:product_id>/reviews", methods=["GET"])
def list_reviews(product_id):
    conn = db.get_db()
    reviews = db.get_reviews_for_product(conn, product_id)
    summary = db.get_review_summary(conn, product_id)
    conn.close()
    return jsonify({"reviews": reviews, "summary": summary})


@app.route("/api/products/<int:product_id>/reviews", methods=["POST"])
@customer_required
def add_review(product_id):
    data = request.get_json(silent=True) or {}
    try:
        rating = int(data.get("rating"))
    except (TypeError, ValueError):
        return jsonify({"error": "Please select a rating."}), 400
    if rating < 1 or rating > 5:
        return jsonify({"error": "Rating must be between 1 and 5."}), 400
    comment = (data.get("comment") or "").strip()[:2000]

    conn = db.get_db()
    product = conn.execute("SELECT id FROM products WHERE id = ? AND is_active = 1", (product_id,)).fetchone()
    if not product:
        conn.close()
        return jsonify({"error": "Product not found."}), 404

    user = db.get_user_by_id(conn, session["user_id"])
    conn.execute(
        "INSERT INTO reviews (product_id, user_id, customer_name, rating, comment) VALUES (?, ?, ?, ?, ?)",
        (product_id, session["user_id"], user["name"], rating, comment),
    )
    conn.commit()
    reviews = db.get_reviews_for_product(conn, product_id)
    summary = db.get_review_summary(conn, product_id)
    conn.close()
    return jsonify({"reviews": reviews, "summary": summary}), 201


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: Admin — Orders management
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/admin/orders", methods=["GET"])
@admin_required
def admin_list_orders():
    conn = db.get_db()
    rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    orders = [db.row_to_order(r, db.get_order_items(conn, r["id"])) for r in rows]
    conn.close()
    return jsonify(orders)


@app.route("/api/admin/orders/<int:order_id>", methods=["PUT"])
@admin_required
def admin_update_order(order_id):
    data = request.get_json(silent=True) or {}
    status = (data.get("orderStatus") or "").strip().lower()
    if status not in VALID_ORDER_STATUSES:
        return jsonify({"error": f"Status must be one of: {', '.join(sorted(VALID_ORDER_STATUSES))}."}), 400

    conn = db.get_db()
    existing = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Order not found."}), 404

    # Restock automatically if an active order is being cancelled.
    if status == "cancelled" and existing["order_status"] != "cancelled":
        for item in db.get_order_items(conn, order_id):
            conn.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (item["quantity"], item["productId"]))

    # Phase 4 CHANGE: shipping confirmation email, only on the transition
    # *into* shipped (so re-saving an already-shipped order, or any other
    # status change, doesn't re-send it).
    just_shipped = status == "shipped" and existing["order_status"] != "shipped"

    conn.execute("UPDATE orders SET order_status = ? WHERE id = ?", (status, order_id))
    conn.commit()

    if just_shipped and existing["user_id"]:
        user = db.get_user_by_id(conn, existing["user_id"])
        if user:
            row_for_mail = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            mailer.send_shipping_confirmation_email(user["email"], db.row_to_order(row_for_mail))

    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    items = db.get_order_items(conn, order_id)
    conn.close()
    return jsonify(db.row_to_order(row, items))


# ─────────────────────────────────────────────────────────────────────────
# CHANGE: Admin — Return / Replacement requests management
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/admin/return-requests", methods=["GET"])
@admin_required
def admin_list_return_requests():
    conn = db.get_db()
    rows = conn.execute(
        """SELECT r.*, o.order_number, o.customer_name, o.customer_phone, o.total
           FROM return_requests r
           JOIN orders o ON o.id = r.order_id
           ORDER BY r.created_at DESC"""
    ).fetchall()
    conn.close()
    return jsonify([
        {
            "id": r["id"],
            "orderId": r["order_id"],
            "orderNumber": r["order_number"],
            "customerName": r["customer_name"],
            "customerPhone": r["customer_phone"],
            "orderTotal": r["total"],
            "type": r["type"],
            "reason": r["reason"] or "",
            "status": r["status"],
            "createdAt": r["created_at"],
        }
        for r in rows
    ])


@app.route("/api/admin/return-requests/<int:request_id>", methods=["PUT"])
@admin_required
def admin_update_return_request(request_id):
    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip().lower()
    if status not in VALID_RETURN_REQUEST_STATUSES:
        return jsonify({"error": f"Status must be one of: {', '.join(sorted(VALID_RETURN_REQUEST_STATUSES))}."}), 400

    conn = db.get_db()
    existing = conn.execute("SELECT * FROM return_requests WHERE id = ?", (request_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Request not found."}), 404

    conn.execute("UPDATE return_requests SET status = ? WHERE id = ?", (status, request_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "status": status})



# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: Admin — Coupons management
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/admin/coupons", methods=["GET"])
@admin_required
def admin_list_coupons():
    conn = db.get_db()
    rows = conn.execute("SELECT * FROM coupons ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify([db.row_to_coupon(r) for r in rows])


@app.route("/api/admin/coupons", methods=["POST"])
@admin_required
def admin_create_coupon():
    data = request.get_json(silent=True) or {}
    cleaned, error = validate_coupon_payload(data, partial=False)
    if error:
        return jsonify({"error": error}), 400
    cleaned.setdefault("min_order", 0)
    cleaned.setdefault("max_uses", None)
    cleaned.setdefault("expires_at", None)
    cleaned.setdefault("is_active", 1)

    conn = db.get_db()
    try:
        cur = conn.execute(
            """INSERT INTO coupons (code, discount_type, discount_value, min_order, max_uses, expires_at, is_active)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (cleaned["code"], cleaned["discount_type"], cleaned["discount_value"], cleaned["min_order"],
             cleaned["max_uses"], cleaned["expires_at"], cleaned["is_active"]),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "A coupon with this code already exists."}), 409

    row = conn.execute("SELECT * FROM coupons WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(db.row_to_coupon(row)), 201


@app.route("/api/admin/coupons/<int:coupon_id>", methods=["PUT"])
@admin_required
def admin_update_coupon(coupon_id):
    conn = db.get_db()
    existing = conn.execute("SELECT id FROM coupons WHERE id = ?", (coupon_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Coupon not found."}), 404

    data = request.get_json(silent=True) or {}
    cleaned, error = validate_coupon_payload(data, partial=True)
    if error:
        conn.close()
        return jsonify({"error": error}), 400
    if not cleaned:
        conn.close()
        return jsonify({"error": "No valid fields to update."}), 400

    set_clause = ", ".join(f"{field} = ?" for field in cleaned.keys())
    params = list(cleaned.values()) + [coupon_id]
    try:
        conn.execute(f"UPDATE coupons SET {set_clause} WHERE id = ?", params)
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "A coupon with this code already exists."}), 409

    row = conn.execute("SELECT * FROM coupons WHERE id = ?", (coupon_id,)).fetchone()
    conn.close()
    return jsonify(db.row_to_coupon(row))


@app.route("/api/admin/coupons/<int:coupon_id>", methods=["DELETE"])
@admin_required
def admin_delete_coupon(coupon_id):
    conn = db.get_db()
    existing = conn.execute("SELECT id FROM coupons WHERE id = ?", (coupon_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Coupon not found."}), 404
    conn.execute("DELETE FROM coupons WHERE id = ?", (coupon_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────
# Phase 5 CHANGE: Banners (homepage promotional strip)
# ─────────────────────────────────────────────────────────────────────────
def validate_banner_payload(data, partial=False):
    cleaned = {}
    if "title" in data or not partial:
        title = (data.get("title") or "").strip()
        if not title:
            return None, "Banner title is required."
        cleaned["title"] = title[:200]
    if "subtitle" in data:
        cleaned["subtitle"] = (data.get("subtitle") or "").strip()[:300]
    if "emoji" in data:
        cleaned["emoji"] = (data.get("emoji") or "🎉").strip()[:8]
    if "linkUrl" in data:
        cleaned["link_url"] = (data.get("linkUrl") or "").strip()[:500]
    if "linkLabel" in data:
        cleaned["link_label"] = (data.get("linkLabel") or "").strip()[:60]
    if "isActive" in data:
        cleaned["is_active"] = 1 if data.get("isActive") else 0
    if "displayOrder" in data:
        try:
            cleaned["display_order"] = int(data.get("displayOrder"))
        except (TypeError, ValueError):
            return None, "Display order must be a number."
    return cleaned, None


@app.route("/api/banners", methods=["GET"])
def public_banners():
    """Only active banners, for the homepage strip — no auth required."""
    conn = db.get_db()
    banners = db.get_active_banners(conn)
    conn.close()
    return jsonify(banners)


@app.route("/api/admin/banners", methods=["GET"])
@admin_required
def admin_list_banners():
    conn = db.get_db()
    banners = db.get_all_banners(conn)
    conn.close()
    return jsonify(banners)


@app.route("/api/admin/banners", methods=["POST"])
@admin_required
def admin_create_banner():
    data = request.get_json(silent=True) or {}
    cleaned, error = validate_banner_payload(data)
    if error:
        return jsonify({"error": error}), 400

    conn = db.get_db()
    cur = conn.execute(
        """INSERT INTO banners (title, subtitle, emoji, link_url, link_label, is_active, display_order)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            cleaned["title"],
            cleaned.get("subtitle", ""),
            cleaned.get("emoji", "🎉"),
            cleaned.get("link_url", ""),
            cleaned.get("link_label", ""),
            cleaned.get("is_active", 1),
            cleaned.get("display_order", 0),
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM banners WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(db.row_to_banner(row)), 201


@app.route("/api/admin/banners/<int:banner_id>", methods=["PUT"])
@admin_required
def admin_update_banner(banner_id):
    conn = db.get_db()
    existing = conn.execute("SELECT id FROM banners WHERE id = ?", (banner_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Banner not found."}), 404

    data = request.get_json(silent=True) or {}
    cleaned, error = validate_banner_payload(data, partial=True)
    if error:
        conn.close()
        return jsonify({"error": error}), 400
    if not cleaned:
        conn.close()
        return jsonify({"error": "No valid fields to update."}), 400

    set_clause = ", ".join(f"{field} = ?" for field in cleaned.keys())
    params = list(cleaned.values()) + [banner_id]
    conn.execute(f"UPDATE banners SET {set_clause} WHERE id = ?", params)
    conn.commit()
    row = conn.execute("SELECT * FROM banners WHERE id = ?", (banner_id,)).fetchone()
    conn.close()
    return jsonify(db.row_to_banner(row))


@app.route("/api/admin/banners/<int:banner_id>", methods=["DELETE"])
@admin_required
def admin_delete_banner(banner_id):
    conn = db.get_db()
    existing = conn.execute("SELECT id FROM banners WHERE id = ?", (banner_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Banner not found."}), 404
    conn.execute("DELETE FROM banners WHERE id = ?", (banner_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────
# Phase 5 CHANGE: Page-view tracking + admin analytics dashboard
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/track/pageview", methods=["POST"])
def track_pageview():
    """Fire-and-forget from the frontend on every page/product view. No
    cookies or identifiers are stored — just a path, an optional product id,
    and a timestamp, purely to power the admin Analytics tab. Failures here
    should never surface to the visitor, so this always returns success."""
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "/")[:300]
    product_id = data.get("productId")
    try:
        product_id = int(product_id) if product_id is not None else None
    except (TypeError, ValueError):
        product_id = None

    try:
        conn = db.get_db()
        db.record_page_view(conn, path, product_id)
        conn.close()
    except Exception:  # noqa: BLE001 — analytics must never break the page for a visitor
        pass
    return jsonify({"success": True})


@app.route("/api/admin/analytics", methods=["GET"])
@admin_required
def admin_analytics():
    try:
        days = int(request.args.get("days", 30))
    except ValueError:
        days = 30
    days = max(7, min(days, 90))

    conn = db.get_db()
    data = db.get_analytics(conn, days=days)
    conn.close()
    return jsonify(data)


# ─────────────────────────────────────────────────────────────────────────
# Phase 6 CHANGE: Admin customer management
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/admin/customers", methods=["GET"])
@admin_required
def admin_list_customers():
    search = (request.args.get("search") or "").strip()[:200]
    conn = db.get_db()
    customers = db.get_all_customers(conn, search=search or None)
    conn.close()
    return jsonify(customers)


@app.route("/api/admin/customers/<int:user_id>", methods=["GET"])
@admin_required
def admin_get_customer(user_id):
    conn = db.get_db()
    customer = db.get_customer_admin_detail(conn, user_id)
    if not customer:
        conn.close()
        return jsonify({"error": "Customer not found."}), 404
    orders = db.get_orders_for_user(conn, user_id)
    conn.close()
    customer["orders"] = orders
    return jsonify(customer)


@app.route("/api/admin/customers/<int:user_id>", methods=["PUT"])
@admin_required
def admin_update_customer(user_id):
    conn = db.get_db()
    existing = db.get_user_by_id(conn, user_id)
    if not existing:
        conn.close()
        return jsonify({"error": "Customer not found."}), 404

    data = request.get_json(silent=True) or {}
    if "isBlocked" in data:
        db.set_customer_blocked(conn, user_id, bool(data.get("isBlocked")))
    if "adminNotes" in data:
        db.set_customer_notes(conn, user_id, (data.get("adminNotes") or "").strip()[:2000])
    conn.commit()

    customer = db.get_customer_admin_detail(conn, user_id)
    conn.close()
    return jsonify(customer)


@app.route("/api/admin/customers/<int:user_id>", methods=["DELETE"])
@admin_required
def admin_delete_customer(user_id):
    conn = db.get_db()
    existing = db.get_user_by_id(conn, user_id)
    if not existing:
        conn.close()
        return jsonify({"error": "Customer not found."}), 404

    # Orders reference user_id without ON DELETE CASCADE (order history is
    # kept intentionally, even for a removed account) — so deleting a
    # customer who has placed any order is refused. Block them instead.
    if db.customer_has_orders(conn, user_id):
        conn.close()
        return jsonify({
            "error": "This customer has order history and can't be deleted. Block the account instead to prevent sign-in."
        }), 400

    db.delete_customer(conn, user_id)
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────
# Phase 6 CHANGE: Contact form
# ─────────────────────────────────────────────────────────────────────────
def validate_contact_payload(data):
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()
    subject = (data.get("subject") or "").strip()
    message = (data.get("message") or "").strip()

    if not name:
        return None, "Please enter your name."
    if not email or not db.EMAIL_RE.match(email):
        return None, "Please enter a valid email address."
    if not message:
        return None, "Please enter a message."
    if len(message) > 5000:
        return None, "Message is too long (5000 characters max)."

    return {
        "name": name[:200],
        "email": email[:200],
        "phone": phone[:30],
        "subject": subject[:200],
        "message": message,
    }, None


@app.route("/api/contact", methods=["POST"])
def submit_contact():
    data = request.get_json(silent=True) or {}
    cleaned, error = validate_contact_payload(data)
    if error:
        return jsonify({"error": error}), 400

    # Rate-limited per IP+email, same mechanism as login throttling, so the
    # form can't be used to spam either the admin inbox or the DB table.
    if rate_limited(login_rate_key("contact:" + cleaned["email"])):
        return jsonify({"error": "Too many messages sent. Please wait a few minutes and try again."}), 429

    conn = db.get_db()
    contact = db.create_contact_message(
        conn, cleaned["name"], cleaned["email"], cleaned["phone"], cleaned["subject"], cleaned["message"]
    )
    conn.close()

    mailer.send_contact_notification_email(app.config.get("CONTACT_NOTIFY_EMAIL"), contact)

    return jsonify({"success": True}), 201


@app.route("/api/admin/contact-messages", methods=["GET"])
@admin_required
def admin_list_contact_messages():
    status = request.args.get("status")
    conn = db.get_db()
    messages = db.get_contact_messages(conn, status=status)
    conn.close()
    return jsonify(messages)


@app.route("/api/admin/contact-messages/<int:message_id>", methods=["PUT"])
@admin_required
def admin_update_contact_message(message_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in {"new", "read", "replied"}:
        return jsonify({"error": "Status must be one of: new, read, replied."}), 400

    conn = db.get_db()
    existing = conn.execute("SELECT id FROM contact_messages WHERE id = ?", (message_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Message not found."}), 404

    db.set_contact_message_status(conn, message_id, status)
    conn.commit()
    row = conn.execute("SELECT * FROM contact_messages WHERE id = ?", (message_id,)).fetchone()
    conn.close()
    return jsonify(db.row_to_contact_message(row))


@app.route("/api/admin/contact-messages/<int:message_id>", methods=["DELETE"])
@admin_required
def admin_delete_contact_message(message_id):
    conn = db.get_db()
    existing = conn.execute("SELECT id FROM contact_messages WHERE id = ?", (message_id,)).fetchone()
    if not existing:
        conn.close()
        return jsonify({"error": "Message not found."}), 404
    db.delete_contact_message(conn, message_id)
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────
# Phase 6 CHANGE: Newsletter
# ─────────────────────────────────────────────────────────────────────────
@app.route("/api/newsletter/subscribe", methods=["POST"])
def newsletter_subscribe():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if not email or not db.EMAIL_RE.match(email):
        return jsonify({"error": "Please enter a valid email address."}), 400

    if rate_limited(login_rate_key("newsletter:" + email)):
        return jsonify({"error": "Too many attempts. Please wait a few minutes and try again."}), 429

    conn = db.get_db()
    subscriber, already = db.subscribe_to_newsletter(conn, email)
    if not already:
        # row_to_subscriber() deliberately omits the raw token from the
        # public dict, so fetch the row directly to build the welcome
        # email's unsubscribe link.
        row = db.get_subscriber_by_email(conn, email)
        conn.close()
        unsubscribe_url = f"{SITE_URL}/api/newsletter/unsubscribe?token={row['unsubscribe_token']}"
        mailer.send_newsletter_welcome_email(email, unsubscribe_url)
    else:
        conn.close()

    return jsonify({"success": True, "alreadySubscribed": already})


@app.route("/api/newsletter/unsubscribe", methods=["GET"])
def newsletter_unsubscribe():
    token = request.args.get("token", "")
    conn = db.get_db()
    found = db.unsubscribe_by_token(conn, token)
    conn.close()
    message = "You've been unsubscribed from the newsletter." if found else "This unsubscribe link is invalid or has already been used."
    # Plain HTML response (not the SPA shell) since this is a one-off link
    # opened straight from an email, not part of the app's normal flow.
    body = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Unsubscribed</title></head>
    <body style="font-family:Georgia,serif;background:#faf6f0;padding:60px 20px;text-align:center;color:#2b2320;">
      <h2 style="color:#8b1e3f;">{html_lib.escape(message)}</h2>
      <p><a href="{SITE_URL}/" style="color:#8b1e3f;">Return to Shri Jeewani Saree Center</a></p>
    </body></html>"""
    return Response(body, mimetype="text/html")


@app.route("/api/admin/newsletter", methods=["GET"])
@admin_required
def admin_list_newsletter():
    active_only = request.args.get("active") == "1"
    conn = db.get_db()
    subscribers = db.get_all_subscribers(conn, active_only=active_only)
    conn.close()

    if request.args.get("format") == "csv":
        lines = ["email,is_active,subscribed_at,unsubscribed_at"]
        for s in subscribers:
            lines.append(
                f"{s['email']},{s['isActive']},{s['subscribedAt']},{s['unsubscribedAt'] or ''}"
            )
        return Response("\n".join(lines), mimetype="text/csv", headers={
            "Content-Disposition": "attachment; filename=newsletter_subscribers.csv"
        })

    return jsonify(subscribers)


@app.route("/api/admin/newsletter/<int:subscriber_id>", methods=["DELETE"])
@admin_required
def admin_delete_subscriber(subscriber_id):
    conn = db.get_db()
    db.delete_subscriber(conn, subscriber_id)
    conn.commit()
    conn.close()
    return jsonify({"success": True})


# ─────────────────────────────────────────────────────────────────────────
# Error handlers
# ─────────────────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found."}), 404
    return render_seo_shell(request.path)


@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "Upload too large. Please choose a smaller image."}), 413


if __name__ == "__main__":
    db.init_db()
    # Security fix: default to debug=False. Running with the Werkzeug
    # debugger enabled on a public-facing server allows arbitrary code
    # execution from any unhandled exception — it must be an explicit
    # opt-in for local development, never the unset-env default.
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=debug_mode)