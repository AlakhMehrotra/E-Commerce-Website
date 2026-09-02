-- Shri Jeevani Sarees — Database Schema
-- SQLite. Tables for every entity in the project roadmap are created now
-- so later phases (cart, orders, reviews, coupons...) never require
-- destructive migrations on top of what Phase 1 ships.

PRAGMA foreign_keys = ON;

-- ───────────────────────── Products ─────────────────────────
CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    price         INTEGER NOT NULL,
    badge         TEXT DEFAULT '',
    emoji         TEXT DEFAULT '🌸',
    description   TEXT DEFAULT '',
    image_data    TEXT,                 -- base64 data URL (Phase 1 storage / primary cover photo)
    images        TEXT DEFAULT '[]',    -- JSON array of base64 data URLs / image URLs for multiple photos
    fabric        TEXT DEFAULT 'Pure Banarasi Silk',
    color         TEXT DEFAULT '',
    stock         INTEGER NOT NULL DEFAULT 25,
    featured      INTEGER NOT NULL DEFAULT 0,   -- 0/1 flags, used by admin "Featured Products"
    bestseller    INTEGER NOT NULL DEFAULT 0,
    new_arrival   INTEGER NOT NULL DEFAULT 0,
    slug          TEXT UNIQUE,           -- for SEO-friendly URLs (Phase 5)
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ───────────────────────── Admin users ─────────────────────────
CREATE TABLE IF NOT EXISTS admin_users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ───────────────────────── Customers ─────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT UNIQUE NOT NULL,
    phone         TEXT,
    password_hash TEXT NOT NULL,
    reset_token   TEXT,
    reset_expires TIMESTAMP,
    -- Login OTP (email-based 2-step verification): a fresh 6-digit code is
    -- issued after a correct password and must be confirmed before a
    -- session is created. login_otp_attempts locks the code out after too
    -- many wrong guesses instead of letting it be brute-forced.
    login_otp_code     TEXT,
    login_otp_expires  TIMESTAMP,
    login_otp_attempts INTEGER NOT NULL DEFAULT 0,
    -- Phase 6: admin customer management. is_blocked prevents sign-in
    -- without deleting the account (which order history references, so a
    -- hard delete is only allowed when a customer has zero orders).
    is_blocked    INTEGER NOT NULL DEFAULT 0,
    blocked_at    TIMESTAMP,
    admin_notes   TEXT DEFAULT '',
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ───────────────────────── Orders (Phase 2/3) ─────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER REFERENCES users(id),
    order_number    TEXT UNIQUE NOT NULL,
    customer_name   TEXT NOT NULL,
    customer_phone  TEXT NOT NULL,
    customer_address TEXT NOT NULL,
    customer_pincode TEXT NOT NULL,
    subtotal        INTEGER NOT NULL,
    tax             INTEGER NOT NULL,
    shipping        INTEGER NOT NULL,
    discount        INTEGER NOT NULL DEFAULT 0,
    total           INTEGER NOT NULL,
    coupon_code     TEXT,
    payment_method  TEXT NOT NULL DEFAULT 'cod',
    payment_status  TEXT NOT NULL DEFAULT 'pending',   -- pending/paid/failed
    razorpay_order_id   TEXT,
    razorpay_payment_id TEXT,
    order_status    TEXT NOT NULL DEFAULT 'placed',    -- placed/shipped/delivered/cancelled
    confirmation_email_sent INTEGER NOT NULL DEFAULT 0, -- Phase 4: idempotency guard (COD + Razorpay verify + webhook can all race to send this)
    -- One-time return/replacement policy: flips to 1 the moment a
    -- return_requests row is created for this order (replace OR return,
    -- whichever comes first — they share the same allowance). This limit is
    -- enforced silently: the customer just stops being offered the option
    -- again, there's no "you already used this" message.
    replacement_used INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS order_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  INTEGER REFERENCES products(id),
    product_name TEXT NOT NULL,
    price       INTEGER NOT NULL,
    quantity    INTEGER NOT NULL
);

-- ───────────────────────── Reviews (Phase 2) ─────────────────────────
CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id),
    customer_name TEXT NOT NULL,
    rating      INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment     TEXT,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ───────────────────────── Coupons (Phase 2) ─────────────────────────
CREATE TABLE IF NOT EXISTS coupons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT UNIQUE NOT NULL,
    discount_type TEXT NOT NULL DEFAULT 'percent',  -- percent/flat
    discount_value INTEGER NOT NULL,
    min_order     INTEGER NOT NULL DEFAULT 0,
    max_uses      INTEGER,
    used_count    INTEGER NOT NULL DEFAULT 0,
    expires_at    TIMESTAMP,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ───────────────────────── Wishlist / Cart (Phase 2) ─────────────────────────
CREATE TABLE IF NOT EXISTS wishlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, product_id)
);

CREATE TABLE IF NOT EXISTS cart_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    quantity    INTEGER NOT NULL DEFAULT 1,
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, product_id)
);

CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id);

-- ───────────────────────── Banners (Phase 5) ─────────────────────────
-- Homepage promotional strip, managed from the admin panel. Kept deliberately
-- simple (text + optional link + emoji) rather than a full image-banner
-- editor, consistent with how badges/emoji are used elsewhere in the catalog.
CREATE TABLE IF NOT EXISTS banners (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    subtitle      TEXT DEFAULT '',
    emoji         TEXT DEFAULT '🎉',
    link_url      TEXT DEFAULT '',
    link_label    TEXT DEFAULT '',
    is_active     INTEGER NOT NULL DEFAULT 1,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ───────────────────────── Page views (Phase 5) ─────────────────────────
-- Minimal, dependency-free analytics: one row per page/product view. No
-- cookies, no fingerprinting, no third-party script — just enough to power
-- the admin Analytics tab (traffic trend, top-viewed products).
CREATE TABLE IF NOT EXISTS page_views (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL,
    product_id  INTEGER REFERENCES products(id),
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_banners_active_order ON banners(is_active, display_order);
CREATE INDEX IF NOT EXISTS idx_page_views_created ON page_views(created_at);
CREATE INDEX IF NOT EXISTS idx_page_views_product ON page_views(product_id);

-- ───────────────────────── Contact messages (Phase 6) ─────────────────────────
-- Submissions from the public "Contact Us" form. Kept in the DB (not just
-- emailed) so a lost/bounced email doesn't lose the enquiry, and so admin
-- has a searchable record of who has been replied to.
CREATE TABLE IF NOT EXISTS contact_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    phone         TEXT DEFAULT '',
    subject       TEXT DEFAULT '',
    message       TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'new',   -- new/read/replied
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_contact_messages_status ON contact_messages(status, created_at);

-- ───────────────────────── Newsletter (Phase 6) ─────────────────────────
-- Simple double-optin-free subscriber list (consistent with this project's
-- "no extra infra" approach — no third-party ESP). unsubscribe_token lets
-- the footer link in emails unsubscribe without requiring a login.
CREATE TABLE IF NOT EXISTS newsletter_subscribers (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    email             TEXT UNIQUE NOT NULL,
    is_active         INTEGER NOT NULL DEFAULT 1,
    unsubscribe_token TEXT UNIQUE NOT NULL,
    subscribed_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    unsubscribed_at   TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_newsletter_active ON newsletter_subscribers(is_active);

-- ───────────────────────── App Settings ─────────────────────────
-- Key-value configuration for integrations.
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ───────────────────────── Admin Notifications ─────────────────────────
-- Real-time notifications for the admin panel (customer logins, signups,
-- new orders, etc.). The bell icon in the admin header polls this table.
CREATE TABLE IF NOT EXISTS admin_notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,                       -- 'customer_login', 'customer_signup', 'new_order', etc.
    title       TEXT NOT NULL,                       -- e.g. "Customer Login: Alakh Mehrotra"
    message     TEXT NOT NULL,                       -- detailed notification body
    is_read     INTEGER NOT NULL DEFAULT 0,          -- 0 = unread, 1 = read
    user_id     INTEGER REFERENCES users(id),        -- the customer this notification is about (nullable)
    metadata    TEXT DEFAULT '{}',                    -- JSON blob for extra data (email, phone, etc.)
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_admin_notif_unread ON admin_notifications(is_read, created_at DESC);

-- ───────────────────────── Return / Replacement requests ─────────────────────────
-- Customer-initiated return or replacement requests, raised from "My Orders"
-- once an order is delivered. Policy: at most ONE return-or-replace request
-- per order (combined allowance, not one of each) — enforced via
-- orders.replacement_used, see that column's comment above.
CREATE TABLE IF NOT EXISTS return_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    user_id     INTEGER REFERENCES users(id),
    type        TEXT NOT NULL,                     -- 'replace' or 'return'
    reason      TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',    -- pending/approved/rejected/completed
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_return_requests_order ON return_requests(order_id);
CREATE INDEX IF NOT EXISTS idx_return_requests_status ON return_requests(status, created_at);
