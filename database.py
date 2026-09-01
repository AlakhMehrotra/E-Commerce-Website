"""
Database access layer for Shri Jeevani Sarees.
Plain sqlite3 (no ORM) — keeps the project dependency-light and easy to
deploy anywhere Python + SQLite run.

Phase 2 CHANGE: added helper functions for customer accounts (users),
server-persisted cart, and wishlist. Nothing in Phase 1 (products, admin
auth) was modified — only additions below.
"""
import sqlite3
import os
import re
import time
import secrets
import datetime
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "jeevani.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

# Default admin credentials (same as the previous admin.html hardcoded
# login) — now stored as a hash, never in plaintext, and overridable via
# environment variables before the first run.
DEFAULT_ADMIN_USER = os.environ.get("JEEVANI_ADMIN_USER", "alakhmehrotra")
DEFAULT_ADMIN_PASS = os.environ.get("JEEVANI_ADMIN_PASS", "alakhmehrotra@123")

# The 12 original catalogue items that used to be hardcoded in saree.js.
# Seeded once on first run so the storefront is never empty.
SEED_PRODUCTS = [
    ('Royal Crimson Bridal', 'bridal', 65000, 'NEW', '👰', 'Premium bridal Banarasi silk saree with handwoven zari work.', 'Pure Banarasi Silk', 'Crimson Red'),
    ('Golden Peacock', 'festive', 28000, '', '🦚', 'A dazzling festive saree with golden peacock motifs.', 'Pure Banarasi Silk', 'Gold'),
    ('Emerald Elegance', 'bridal', 72000, 'BESTSELLER', '💚', 'Lush emerald bridal saree with intricate zari border.', 'Pure Banarasi Silk', 'Emerald Green'),
    ('Classic Maroon', 'classic', 38000, '', '🌹', 'A timeless maroon silk saree for every occasion.', 'Pure Banarasi Silk', 'Maroon'),
    ('Midnight Blue', 'contemporary', 32000, '', '🌙', 'Contemporary midnight blue saree with modern weave patterns.', 'Pure Banarasi Silk', 'Midnight Blue'),
    ('Rose Gold Shimmer', 'festive', 45000, 'NEW', '✨', 'Shimmering rose gold festive saree with luxurious finish.', 'Pure Banarasi Silk', 'Rose Gold'),
    ('Heritage Banarasi', 'classic', 55000, '', '🕉️', 'A heritage classic with traditional Banarasi motifs.', 'Pure Banarasi Silk', 'Heritage Gold'),
    ('Ivory Bridal', 'bridal', 68000, '', '🤍', 'Elegant ivory bridal saree with fine silver zari work.', 'Pure Banarasi Silk', 'Ivory'),
    ('Magenta Magic', 'festive', 29000, 'SALE', '💗', 'Vibrant magenta festive saree at a special price.', 'Pure Banarasi Silk', 'Magenta'),
    ('Teal Treasure', 'contemporary', 35000, '', '🦋', 'Beautiful teal saree with contemporary butterfly motifs.', 'Pure Banarasi Silk', 'Teal'),
    ('Burgundy Royal', 'bridal', 78000, 'PREMIUM', '👑', 'The most premium bridal saree with royal burgundy hue.', 'Pure Banarasi Silk', 'Burgundy'),
    ('Saffron Silk', 'classic', 42000, '', '🧡', 'Classic saffron silk saree, auspicious and timeless.', 'Pure Banarasi Silk', 'Saffron'),
]

# Starter coupons seeded once on first run. Add more any time from Admin → Coupons.
SEED_COUPONS = [
    ("WELCOME10",   "percent", 10,   0,     None),  # 10% off any order
    ("FESTIVE2000", "flat",    2000, 25000, None),  # ₹2000 off orders above ₹25,000
    ("SAREE500",    "flat",    500,  10000, None),  # ₹500 off orders above ₹10,000
]


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def slugify(name, pid):
    slug = "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return f"{slug}-{pid}"


def _ensure_column(conn, table, column, ddl_type_and_default):
    """Phase 4 CHANGE: lightweight, non-destructive migration helper.
    `CREATE TABLE IF NOT EXISTS` in schema.sql only helps brand-new
    databases — an existing jeevani.db from an earlier phase needs an
    explicit ALTER TABLE to gain a new column. Safe to call every startup;
    no-ops if the column already exists."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type_and_default}")


def init_db():
    """Create tables (if missing) and seed initial data. Safe to call every startup."""
    conn = get_db()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()

    # Phase 4 CHANGE: migrate any pre-existing database forward.
    _ensure_column(conn, "orders", "confirmation_email_sent", "INTEGER NOT NULL DEFAULT 0")
    # Phase 6 CHANGE: customer management columns on an existing users table.
    _ensure_column(conn, "users", "is_blocked", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "users", "blocked_at", "TIMESTAMP")
    _ensure_column(conn, "users", "admin_notes", "TEXT DEFAULT ''")
    # CHANGE: login OTP (email-based 2-step verification) columns on an
    # existing users table.
    _ensure_column(conn, "users", "login_otp_code", "TEXT")
    _ensure_column(conn, "users", "login_otp_expires", "TIMESTAMP")
    _ensure_column(conn, "users", "login_otp_attempts", "INTEGER NOT NULL DEFAULT 0")
    # CHANGE: one-time return/replacement allowance flag on an existing
    # orders table (see schema.sql for the full comment).
    _ensure_column(conn, "orders", "replacement_used", "INTEGER NOT NULL DEFAULT 0")
    conn.commit()

    # Seed products only if the table is empty
    count = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    if count == 0:
        for p in SEED_PRODUCTS:
            cur = conn.execute(
                """INSERT INTO products
                   (name, category, price, badge, emoji, description, fabric, color)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                p,
            )
            pid = cur.lastrowid
            conn.execute("UPDATE products SET slug = ? WHERE id = ?", (slugify(p[0], pid), pid))
        conn.commit()

    # Seed default admin account only if no admin exists yet
    admin_count = conn.execute("SELECT COUNT(*) AS c FROM admin_users").fetchone()["c"]
    if admin_count == 0:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            (DEFAULT_ADMIN_USER, generate_password_hash(DEFAULT_ADMIN_PASS)),
        )
        conn.commit()

    # Seed starter coupons only if none exist yet — won't touch coupons you've
    # already created in the admin panel on a running instance.
    coupon_count = conn.execute("SELECT COUNT(*) AS c FROM coupons").fetchone()["c"]
    if coupon_count == 0:
        for code, dtype, dvalue, min_order, max_uses in SEED_COUPONS:
            conn.execute(
                """INSERT INTO coupons (code, discount_type, discount_value, min_order, max_uses)
                   VALUES (?, ?, ?, ?, ?)""",
                (code, dtype, dvalue, min_order, max_uses),
            )
        conn.commit()

    conn.close()


def row_to_product(row):
    """Convert a DB row into the JSON shape the frontend (saree.js) expects."""
    return {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "price": row["price"],
        "badge": row["badge"] or "",
        "emoji": row["emoji"] or "🌸",
        "description": row["description"] or "",
        "imageData": row["image_data"],
        "fabric": row["fabric"] or "Pure Banarasi Silk",
        "color": row["color"] or "",
        "stock": row["stock"],
        "featured": bool(row["featured"]),
        "bestseller": bool(row["bestseller"]),
        "newArrival": bool(row["new_arrival"]),
        "slug": row["slug"],
    }


# ─────────────────────────────────────────────────────────────────────────
# Phase 2: Customer accounts
# ─────────────────────────────────────────────────────────────────────────
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def row_to_user(row):
    """Public-safe user shape — never includes password_hash or reset_token."""
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"] or "",
    }


def get_user_by_id(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_user_by_identifier(conn, identifier):
    """Look a user up by email OR phone, used for the sign-in form."""
    return conn.execute(
        "SELECT * FROM users WHERE email = ? OR phone = ?", (identifier, identifier)
    ).fetchone()


def email_exists(conn, email):
    return conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone() is not None


# ─────────────────────────────────────────────────────────────────────────
# Phase 6: Admin customer management
# ─────────────────────────────────────────────────────────────────────────
def row_to_customer_admin(row):
    """Admin-facing customer shape — includes moderation fields that
    row_to_user() deliberately omits from the public/customer-facing API."""
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"] or "",
        "isBlocked": bool(row["is_blocked"]),
        "blockedAt": row["blocked_at"],
        "adminNotes": row["admin_notes"] or "",
        "createdAt": row["created_at"],
        "orderCount": row["order_count"] if "order_count" in row.keys() else None,
        "totalSpent": row["total_spent"] if "total_spent" in row.keys() else None,
    }


def get_all_customers(conn, search=None):
    """Every customer with aggregated order stats, newest first. `search`
    matches name/email/phone (case-insensitive substring)."""
    sql = """
        SELECT u.*,
               COUNT(o.id) AS order_count,
               COALESCE(SUM(CASE WHEN o.payment_status = 'paid' OR o.payment_method = 'cod'
                                  THEN o.total ELSE 0 END), 0) AS total_spent
        FROM users u
        LEFT JOIN orders o ON o.user_id = u.id
    """
    params = []
    if search:
        sql += " WHERE u.name LIKE ? OR u.email LIKE ? OR u.phone LIKE ?"
        like = f"%{search}%"
        params = [like, like, like]
    sql += " GROUP BY u.id ORDER BY u.created_at DESC"
    rows = conn.execute(sql, params).fetchall()
    return [row_to_customer_admin(r) for r in rows]


def get_customer_admin_detail(conn, user_id):
    row = conn.execute(
        """SELECT u.*,
                  COUNT(o.id) AS order_count,
                  COALESCE(SUM(CASE WHEN o.payment_status = 'paid' OR o.payment_method = 'cod'
                                     THEN o.total ELSE 0 END), 0) AS total_spent
           FROM users u LEFT JOIN orders o ON o.user_id = u.id
           WHERE u.id = ? GROUP BY u.id""",
        (user_id,),
    ).fetchone()
    return row_to_customer_admin(row) if row else None


def set_customer_blocked(conn, user_id, blocked):
    conn.execute(
        "UPDATE users SET is_blocked = ?, blocked_at = ? WHERE id = ?",
        (1 if blocked else 0, datetime.datetime.now().isoformat() if blocked else None, user_id),
    )


def set_customer_notes(conn, user_id, notes):
    conn.execute("UPDATE users SET admin_notes = ? WHERE id = ?", (notes, user_id))


def customer_has_orders(conn, user_id):
    row = conn.execute("SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (user_id,)).fetchone()
    return row["c"] > 0


def delete_customer(conn, user_id):
    """Only safe to call after customer_has_orders() returns False — orders
    reference users without ON DELETE CASCADE, so deleting a customer with
    order history would violate the foreign key. Cart/wishlist/reviews do
    cascade, so those clean up automatically."""
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# ─────────────────────────────────────────────────────────────────────────
# Phase 4: Password reset tokens
# ─────────────────────────────────────────────────────────────────────────
RESET_TOKEN_TTL_MINUTES = 30


def set_reset_token(conn, user_id, token):
    expires = (datetime.datetime.now() + datetime.timedelta(minutes=RESET_TOKEN_TTL_MINUTES)).isoformat()
    conn.execute(
        "UPDATE users SET reset_token = ?, reset_expires = ? WHERE id = ?",
        (token, expires, user_id),
    )
    return expires


def get_user_by_reset_token(conn, token):
    """Returns the user row if the token exists AND hasn't expired, else None."""
    if not token:
        return None
    row = conn.execute("SELECT * FROM users WHERE reset_token = ?", (token,)).fetchone()
    if not row:
        return None
    if not row["reset_expires"]:
        return None
    try:
        expires = datetime.datetime.fromisoformat(row["reset_expires"])
    except ValueError:
        return None
    if datetime.datetime.now() > expires:
        return None
    return row


def clear_reset_token(conn, user_id):
    conn.execute("UPDATE users SET reset_token = NULL, reset_expires = NULL WHERE id = ?", (user_id,))


# ─────────────────────────────────────────────────────────────────────────
# CHANGE: Login OTP — every customer login now needs a 6-digit code
# emailed to the account after the password check, confirmed before a
# session is created. Separate from the password-reset token above (that's
# for a forgotten password; this runs on every normal sign-in).
# ─────────────────────────────────────────────────────────────────────────
LOGIN_OTP_TTL_MINUTES = 5
LOGIN_OTP_MAX_ATTEMPTS = 5


def set_login_otp(conn, user_id, otp_code):
    """Stores a fresh code and resets the attempt counter, so requesting a
    new code (sign-in retry or explicit resend) always gives a clean slate
    rather than inheriting a previous lockout."""
    expires = (datetime.datetime.now() + datetime.timedelta(minutes=LOGIN_OTP_TTL_MINUTES)).isoformat()
    conn.execute(
        "UPDATE users SET login_otp_code = ?, login_otp_expires = ?, login_otp_attempts = 0 WHERE id = ?",
        (otp_code, expires, user_id),
    )
    return expires


def verify_login_otp(conn, user_id, submitted_code):
    """Checks a submitted code against the stored one. Returns one of:
    'ok', 'expired', 'invalid', or 'locked' (too many wrong guesses — the
    customer must request a new code via resend). A wrong guess always
    increments the attempt counter first, so this can't be looped forever."""
    row = conn.execute(
        "SELECT login_otp_code, login_otp_expires, login_otp_attempts FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not row or not row["login_otp_code"]:
        return "invalid"
    if row["login_otp_attempts"] >= LOGIN_OTP_MAX_ATTEMPTS:
        return "locked"
    if not row["login_otp_expires"]:
        return "invalid"
    try:
        expires = datetime.datetime.fromisoformat(row["login_otp_expires"])
    except ValueError:
        return "invalid"
    if datetime.datetime.now() > expires:
        return "expired"
    if not hmac_compare(submitted_code, row["login_otp_code"]):
        conn.execute("UPDATE users SET login_otp_attempts = login_otp_attempts + 1 WHERE id = ?", (user_id,))
        return "invalid"
    return "ok"


def clear_login_otp(conn, user_id):
    conn.execute(
        "UPDATE users SET login_otp_code = NULL, login_otp_expires = NULL, login_otp_attempts = 0 WHERE id = ?",
        (user_id,),
    )


def hmac_compare(a, b):
    """Constant-time string comparison for the OTP code, same reasoning as
    comparing password hashes — no need to leak timing information about
    how many leading digits matched."""
    import hmac as _hmac
    return _hmac.compare_digest(str(a or ""), str(b or ""))


# ─────────────────────────────────────────────────────────────────────────
# Phase 2: Cart (server-persisted per logged-in customer)
# ─────────────────────────────────────────────────────────────────────────
def row_to_cart_item(row):
    """Cart row joined with product data, shaped exactly like the product
    objects the frontend already knows how to render in cart/checkout."""
    return {
        "id": row["product_id"],
        "name": row["name"],
        "price": row["price"],
        "emoji": row["emoji"] or "🌸",
        "imageData": row["image_data"],
        "stock": row["stock"],
        "quantity": row["quantity"],
    }


def get_cart_items(conn, user_id):
    rows = conn.execute(
        """SELECT c.product_id, c.quantity, p.name, p.price, p.emoji, p.image_data, p.stock
           FROM cart_items c
           JOIN products p ON p.id = c.product_id
           WHERE c.user_id = ? AND p.is_active = 1
           ORDER BY c.created_at ASC""",
        (user_id,),
    ).fetchall()
    return [row_to_cart_item(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────
# Phase 2: Wishlist (server-persisted per logged-in customer)
# ─────────────────────────────────────────────────────────────────────────
def get_wishlist_items(conn, user_id):
    rows = conn.execute(
        """SELECT p.* FROM wishlist w
           JOIN products p ON p.id = w.product_id
           WHERE w.user_id = ? AND p.is_active = 1
           ORDER BY w.created_at DESC""",
        (user_id,),
    ).fetchall()
    return [row_to_product(r) for r in rows]


def get_wishlist_ids(conn, user_id):
    rows = conn.execute("SELECT product_id FROM wishlist WHERE user_id = ?", (user_id,)).fetchall()
    return [r["product_id"] for r in rows]


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: Orders / Checkout
# ─────────────────────────────────────────────────────────────────────────
def generate_order_number():
    """e.g. SJS260701A1B2C3 — sortable-ish, unique enough without a UUID."""
    return f"SJS{time.strftime('%y%m%d')}{secrets.token_hex(3).upper()}"


def row_to_order_item(row):
    return {
        "productId": row["product_id"],
        "name": row["product_name"],
        "price": row["price"],
        "quantity": row["quantity"],
    }


def get_order_items(conn, order_id):
    rows = conn.execute(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id ASC", (order_id,)
    ).fetchall()
    return [row_to_order_item(r) for r in rows]


def row_to_order(row, items=None):
    order = {
        "id": row["id"],
        "orderNumber": row["order_number"],
        "customerName": row["customer_name"],
        "customerPhone": row["customer_phone"],
        "customerAddress": row["customer_address"],
        "customerPincode": row["customer_pincode"],
        "subtotal": row["subtotal"],
        "tax": row["tax"],
        "shipping": row["shipping"],
        "discount": row["discount"],
        "total": row["total"],
        "couponCode": row["coupon_code"] or "",
        "paymentMethod": row["payment_method"],
        "paymentStatus": row["payment_status"],
        "orderStatus": row["order_status"],
        # CHANGE: exposed so the frontend can decide whether to show the
        # "Request Replacement / Return" option — never rendered as a
        # message like "you already used this", just used to hide the button.
        "replacementUsed": bool(row["replacement_used"]) if "replacement_used" in row.keys() else False,
        "createdAt": row["created_at"],
    }
    if items is not None:
        order["items"] = items
    return order


def get_orders_for_user(conn, user_id):
    rows = conn.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    return [row_to_order(r, get_order_items(conn, r["id"])) for r in rows]


def get_order_by_number(conn, order_number, user_id=None):
    if user_id is not None:
        return conn.execute(
            "SELECT * FROM orders WHERE order_number = ? AND user_id = ?", (order_number, user_id)
        ).fetchone()
    return conn.execute("SELECT * FROM orders WHERE order_number = ?", (order_number,)).fetchone()


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: Coupons
# ─────────────────────────────────────────────────────────────────────────
def row_to_coupon(row):
    return {
        "id": row["id"],
        "code": row["code"],
        "discountType": row["discount_type"],
        "discountValue": row["discount_value"],
        "minOrder": row["min_order"],
        "maxUses": row["max_uses"],
        "usedCount": row["used_count"],
        "expiresAt": row["expires_at"],
        "isActive": bool(row["is_active"]),
        "createdAt": row["created_at"],
    }


def find_active_coupon(conn, code):
    return conn.execute(
        "SELECT * FROM coupons WHERE code = ? AND is_active = 1", ((code or "").strip().upper(),)
    ).fetchone()


def compute_coupon_discount(coupon_row, subtotal):
    """Returns (discount_amount, error_message). error_message is None if the
    coupon is currently usable against this subtotal."""
    if coupon_row["min_order"] and subtotal < coupon_row["min_order"]:
        return 0, f"This coupon needs a minimum order of ₹{coupon_row['min_order']:,}."
    if coupon_row["max_uses"] is not None and coupon_row["used_count"] >= coupon_row["max_uses"]:
        return 0, "This coupon has reached its usage limit."
    if coupon_row["expires_at"]:
        try:
            expires = datetime.datetime.fromisoformat(coupon_row["expires_at"])
            if datetime.datetime.now() > expires:
                return 0, "This coupon has expired."
        except ValueError:
            pass  # malformed date — don't block the customer over an admin data issue

    if coupon_row["discount_type"] == "flat":
        discount = coupon_row["discount_value"]
    else:
        discount = round(subtotal * coupon_row["discount_value"] / 100)

    return min(discount, subtotal), None


# ─────────────────────────────────────────────────────────────────────────
# Phase 2 CHANGE: Reviews
# ─────────────────────────────────────────────────────────────────────────
def row_to_review(row):
    return {
        "id": row["id"],
        "productId": row["product_id"],
        "customerName": row["customer_name"],
        "rating": row["rating"],
        "comment": row["comment"] or "",
        "createdAt": row["created_at"],
    }


def get_reviews_for_product(conn, product_id):
    rows = conn.execute(
        "SELECT * FROM reviews WHERE product_id = ? ORDER BY created_at DESC", (product_id,)
    ).fetchall()
    return [row_to_review(r) for r in rows]


def get_review_summary(conn, product_id):
    row = conn.execute(
        "SELECT COUNT(*) AS c, AVG(rating) AS avg FROM reviews WHERE product_id = ?", (product_id,)
    ).fetchone()
    return {"count": row["c"], "average": round(row["avg"], 1) if row["avg"] else 0}


# ─────────────────────────────────────────────────────────────────────────
# Phase 5: Banners
# ─────────────────────────────────────────────────────────────────────────
def row_to_banner(row):
    return {
        "id": row["id"],
        "title": row["title"],
        "subtitle": row["subtitle"] or "",
        "emoji": row["emoji"] or "🎉",
        "linkUrl": row["link_url"] or "",
        "linkLabel": row["link_label"] or "",
        "isActive": bool(row["is_active"]),
        "displayOrder": row["display_order"],
        "createdAt": row["created_at"],
    }


def get_active_banners(conn):
    rows = conn.execute(
        "SELECT * FROM banners WHERE is_active = 1 ORDER BY display_order ASC, id ASC"
    ).fetchall()
    return [row_to_banner(r) for r in rows]


def get_all_banners(conn):
    rows = conn.execute("SELECT * FROM banners ORDER BY display_order ASC, id ASC").fetchall()
    return [row_to_banner(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────
# Phase 5: Page views / analytics
# ─────────────────────────────────────────────────────────────────────────
def record_page_view(conn, path, product_id=None):
    conn.execute("INSERT INTO page_views (path, product_id) VALUES (?, ?)", (path, product_id))
    conn.commit()


def _daily_series(conn, sql, params, days):
    """Runs a `GROUP BY date(...)` query and fills in zero-value days so the
    admin charts don't have gaps for days with no activity."""
    rows = conn.execute(sql, params).fetchall()
    by_day = {r["day"]: r["value"] for r in rows}
    today = datetime.date.today()
    series = []
    for i in range(days - 1, -1, -1):
        day = (today - datetime.timedelta(days=i)).isoformat()
        series.append({"date": day, "value": by_day.get(day, 0)})
    return series


def get_analytics(conn, days=30):
    """Everything the admin Analytics tab needs, computed on demand from
    existing tables (orders/order_items/users/page_views) — no separate
    aggregation/rollup job required at this project's scale."""
    since = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()

    revenue_series = _daily_series(
        conn,
        """SELECT date(created_at) AS day, SUM(total) AS value FROM orders
           WHERE (payment_status = 'paid' OR payment_method = 'cod')
           AND date(created_at) >= ? GROUP BY day""",
        (since,),
        days,
    )
    orders_series = _daily_series(
        conn,
        """SELECT date(created_at) AS day, COUNT(*) AS value FROM orders
           WHERE date(created_at) >= ? GROUP BY day""",
        (since,),
        days,
    )
    signups_series = _daily_series(
        conn,
        """SELECT date(created_at) AS day, COUNT(*) AS value FROM users
           WHERE date(created_at) >= ? GROUP BY day""",
        (since,),
        days,
    )
    views_series = _daily_series(
        conn,
        """SELECT date(created_at) AS day, COUNT(*) AS value FROM page_views
           WHERE date(created_at) >= ? GROUP BY day""",
        (since,),
        days,
    )

    top_by_revenue = conn.execute(
        """SELECT p.id, p.name, SUM(oi.price * oi.quantity) AS revenue, SUM(oi.quantity) AS units
           FROM order_items oi
           JOIN orders o ON o.id = oi.order_id
           LEFT JOIN products p ON p.id = oi.product_id
           WHERE o.payment_status = 'paid' OR o.payment_method = 'cod'
           GROUP BY oi.product_id ORDER BY revenue DESC LIMIT 5"""
    ).fetchall()

    top_by_views = conn.execute(
        """SELECT p.id, p.name, COUNT(*) AS views
           FROM page_views pv JOIN products p ON p.id = pv.product_id
           WHERE pv.product_id IS NOT NULL AND date(pv.created_at) >= ?
           GROUP BY pv.product_id ORDER BY views DESC LIMIT 5""",
        (since,),
    ).fetchall()

    status_rows = conn.execute(
        "SELECT order_status, COUNT(*) AS c FROM orders GROUP BY order_status"
    ).fetchall()

    totals = conn.execute(
        """SELECT
             (SELECT COALESCE(SUM(total), 0) FROM orders WHERE payment_status = 'paid' OR payment_method = 'cod') AS revenue,
             (SELECT COUNT(*) FROM orders) AS orders,
             (SELECT COUNT(*) FROM users) AS customers"""
    ).fetchone()
    order_count = totals["orders"] or 0
    avg_order_value = round(totals["revenue"] / order_count) if order_count else 0

    return {
        "days": days,
        "revenueSeries": revenue_series,
        "ordersSeries": orders_series,
        "signupsSeries": signups_series,
        "viewsSeries": views_series,
        "topProductsByRevenue": [
            {"id": r["id"], "name": r["name"] or "(deleted product)", "revenue": r["revenue"], "units": r["units"]}
            for r in top_by_revenue
        ],
        "topProductsByViews": [{"id": r["id"], "name": r["name"], "views": r["views"]} for r in top_by_views],
        "orderStatusCounts": {r["order_status"]: r["c"] for r in status_rows},
        "totals": {
            "revenue": totals["revenue"],
            "orders": order_count,
            "customers": totals["customers"],
            "avgOrderValue": avg_order_value,
            "viewsInWindow": sum(p["value"] for p in views_series),
        },
    }


# ─────────────────────────────────────────────────────────────────────────
# Phase 6: Contact form
# ─────────────────────────────────────────────────────────────────────────
def row_to_contact_message(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"] or "",
        "subject": row["subject"] or "",
        "message": row["message"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }


def create_contact_message(conn, name, email, phone, subject, message):
    cur = conn.execute(
        """INSERT INTO contact_messages (name, email, phone, subject, message)
           VALUES (?, ?, ?, ?, ?)""",
        (name, email, phone, subject, message),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM contact_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    return row_to_contact_message(row)


def get_contact_messages(conn, status=None):
    if status:
        rows = conn.execute(
            "SELECT * FROM contact_messages WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM contact_messages ORDER BY created_at DESC").fetchall()
    return [row_to_contact_message(r) for r in rows]


def set_contact_message_status(conn, message_id, status):
    conn.execute("UPDATE contact_messages SET status = ? WHERE id = ?", (status, message_id))


def delete_contact_message(conn, message_id):
    conn.execute("DELETE FROM contact_messages WHERE id = ?", (message_id,))


# ─────────────────────────────────────────────────────────────────────────
# Phase 6: Newsletter
# ─────────────────────────────────────────────────────────────────────────
def row_to_subscriber(row):
    return {
        "id": row["id"],
        "email": row["email"],
        "isActive": bool(row["is_active"]),
        "subscribedAt": row["subscribed_at"],
        "unsubscribedAt": row["unsubscribed_at"],
    }


def get_subscriber_by_email(conn, email):
    return conn.execute("SELECT * FROM newsletter_subscribers WHERE email = ?", (email,)).fetchone()


def subscribe_to_newsletter(conn, email):
    """Idempotent: re-subscribing a previously-unsubscribed email reactivates
    it instead of erroring on the UNIQUE constraint. Returns
    (subscriber_dict, already_subscribed_bool)."""
    existing = get_subscriber_by_email(conn, email)
    if existing:
        if existing["is_active"]:
            return row_to_subscriber(existing), True
        conn.execute(
            "UPDATE newsletter_subscribers SET is_active = 1, subscribed_at = CURRENT_TIMESTAMP, "
            "unsubscribed_at = NULL WHERE id = ?",
            (existing["id"],),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM newsletter_subscribers WHERE id = ?", (existing["id"],)).fetchone()
        return row_to_subscriber(row), False

    token = secrets.token_urlsafe(24)
    cur = conn.execute(
        "INSERT INTO newsletter_subscribers (email, unsubscribe_token) VALUES (?, ?)",
        (email, token),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM newsletter_subscribers WHERE id = ?", (cur.lastrowid,)).fetchone()
    return row_to_subscriber(row), False


def unsubscribe_by_token(conn, token):
    row = conn.execute(
        "SELECT * FROM newsletter_subscribers WHERE unsubscribe_token = ?", (token,)
    ).fetchone()
    if not row:
        return False
    conn.execute(
        "UPDATE newsletter_subscribers SET is_active = 0, unsubscribed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (row["id"],),
    )
    conn.commit()
    return True


def get_all_subscribers(conn, active_only=False):
    if active_only:
        rows = conn.execute(
            "SELECT * FROM newsletter_subscribers WHERE is_active = 1 ORDER BY subscribed_at DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM newsletter_subscribers ORDER BY subscribed_at DESC"
        ).fetchall()
    return [row_to_subscriber(r) for r in rows]


def delete_subscriber(conn, subscriber_id):
    conn.execute("DELETE FROM newsletter_subscribers WHERE id = ?", (subscriber_id,))


# ─────────────────────────────────────────────────────────────────────────
# App Settings
# ─────────────────────────────────────────────────────────────────────────
def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row["value"]
    return default


def get_all_settings(conn):
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_setting(conn, key, value):
    conn.execute(
        """INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
        (key, str(value)),
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────
# Admin Notifications
# ─────────────────────────────────────────────────────────────────────────
def create_notification(conn, notif_type, title, message, user_id=None, metadata=None):
    """Insert a new admin notification."""
    import json
    meta_json = json.dumps(metadata) if metadata else "{}"
    conn.execute(
        """INSERT INTO admin_notifications (type, title, message, user_id, metadata)
           VALUES (?, ?, ?, ?, ?)""",
        (notif_type, title, message, user_id, meta_json),
    )
    conn.commit()


def get_notifications(conn, unread_only=False, limit=50):
    """Fetch admin notifications, optionally filtered to unread only."""
    import json
    if unread_only:
        rows = conn.execute(
            "SELECT * FROM admin_notifications WHERE is_read = 0 ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM admin_notifications ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    result = []
    for r in rows:
        meta = {}
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        result.append({
            "id": r["id"],
            "type": r["type"],
            "title": r["title"],
            "message": r["message"],
            "isRead": bool(r["is_read"]),
            "userId": r["user_id"],
            "metadata": meta,
            "createdAt": r["created_at"],
        })
    return result


def get_unread_notification_count(conn):
    """Return the number of unread admin notifications."""
    row = conn.execute("SELECT COUNT(*) AS cnt FROM admin_notifications WHERE is_read = 0").fetchone()
    return row["cnt"] if row else 0


def mark_notification_read(conn, notif_id):
    """Mark a single notification as read."""
    conn.execute("UPDATE admin_notifications SET is_read = 1 WHERE id = ?", (notif_id,))
    conn.commit()


def mark_all_notifications_read(conn):
    """Mark all notifications as read."""
    conn.execute("UPDATE admin_notifications SET is_read = 1 WHERE is_read = 0")
    conn.commit()


