# Shri Jeevani Sarees — Backend (Flask + SQLite)

Real backend behind your existing, unchanged storefront and admin UI.

## Phases shipped
- **Phase 1** — Products, admin auth, SQLite schema for everything downstream.
- **Phase 2** — Customer accounts, server-persisted cart/wishlist, coupons,
  checkout → real orders, order tracking, My Orders, My Profile, reviews,
  related products, recently viewed, and Admin Orders/Coupons tabs.
- **Phase 3** — Razorpay checkout for UPI/Card (Cash on Delivery has always
  worked without any extra setup), with a dedicated payment success/failed
  flow.
- **Phase 4** — Transactional email via SMTP (Flask-Mail): order
  confirmation, shipping confirmation, and a real forgot/reset password
  flow.
- **Phase 5** — SEO (dynamic per-product URLs with server-rendered meta
  tags, sitemap.xml, robots.txt) and admin extras (banner management,
  Featured/Bestseller/New Arrival flags, analytics dashboard).

## What changed in this pass (Phase 4 — Email)
- **Order confirmation email** — sent the moment a Cash on Delivery order is
  placed, or the moment a UPI/Card order's payment is verified (whichever
  happens first: the customer's browser calling `/verify`, or the Razorpay
  webhook — an idempotency flag on the order guarantees only one email goes
  out even if both fire).
- **Shipping confirmation email** — sent when an admin moves an order to
  "Shipped" in the Admin → Orders tab. Only fires on the transition into
  Shipped, so re-saving an already-shipped order doesn't send a duplicate.
- **Forgot / reset password** — the "Forgot password?" link on the sign-in
  form now works end-to-end: it emails a time-limited reset link
  (`/saree.html?resetToken=...`), which opens the login modal straight to a
  "choose a new password" form. Reset tokens expire after 30 minutes and are
  single-use. The forgot-password endpoint always returns the same response
  whether or not the email has an account, and is rate-limited, so it can't
  be used to find out which emails are registered or to spam a mailbox.
- All emails are sent on a background thread so a slow or unreachable SMTP
  server never adds latency to (or breaks) checkout, admin order updates, or
  sign-in. If `MAIL_SERVER` isn't set, sending is skipped and logged instead
  of raising — the same "degrade gracefully" approach already used for
  Razorpay.

## What changed in this pass (Phase 5 — SEO + Admin extras)
- **Dynamic product URLs** — every product now has a real URL,
  `/product/<slug>` (e.g. `/product/royal-crimson-bridal-1`), instead of
  living only behind a client-side modal with no URL of its own. Flask
  serves the same SPA shell for this route, but with that product's
  title/description/price injected server-side, and boots the page
  straight into that product's detail view (no extra redirect/flicker).
  Clicking a product from any grid, and Back/Forward, update the address
  bar the same way.
- **Meta tags / Open Graph / Twitter Cards** — every page now has a real
  `<meta name="description">`, canonical URL, Open Graph and Twitter Card
  tags, and a JSON-LD Organization block. Product pages get their own
  title/description/price so a link shared on WhatsApp, Twitter, etc.
  unfurls with the right saree, not a generic homepage card. (Product
  photos are stored as base64 data URLs, which social platforms can't
  fetch as an `og:image` — those pages fall back to the homepage hero
  photo instead.)
- **sitemap.xml / robots.txt** — `/sitemap.xml` lists the homepage plus
  every active product's URL (regenerated on each request, so it's always
  current); `/robots.txt` allows everything except `/admin.html` and
  `/api/`, and points crawlers at the sitemap.
- **Banner management** — a new **Banners** tab in the admin panel manages
  a promotional strip shown at the top of the homepage (title, subtitle,
  emoji, optional link, display order, active/disabled). Multiple active
  banners rotate automatically.
- **Featured / Bestseller / New Arrival flags** — the product form gained
  three checkboxes (the underlying columns already existed in the schema
  from Phase 1, just unused until now). Checking "Bestseller" or "New
  Arrival" adds that product to new **Bestsellers** / **New Arrivals** rows
  on the homepage, hidden automatically when nothing is flagged.
- **Analytics dashboard** — a new **Analytics** tab shows total revenue,
  orders, customers, and average order value; day-by-day revenue, orders,
  and page-view charts (7/30/90-day toggle); an order-status breakdown; and
  top products by revenue and by views. Page views are recorded via a
  small, cookie-free `/api/track/pageview` call the frontend fires on every
  page/product view — no third-party analytics script involved.

## What changed in this pass
- Checkout now requires a signed-in account (orders are tied to a customer
  record) — the same rule already applied to cart/wishlist.
- Checkout modal gained a coupon code field; the order summary reflects the
  discount live.
- "Place Order" persists a real order (`orders` + `order_items` tables),
  decrements stock in a transaction, and clears the cart.
- UPI/Card payment methods open the Razorpay checkout widget; the order is
  only marked paid after the server verifies the payment signature. On
  cancellation or failure, stock **and the cart itself** are restored so
  "Try Again" has the customer's items ready to go.
- Successful orders land on a dedicated **Order Confirmed** page; failed or
  cancelled payments land on a **Payment Not Completed** page with a
  one-click retry.
- The login modal's signed-in view now shows **My Orders** with a simple
  tracking progress bar (Placed → Shipped → Delivered, or Cancelled), and a
  **My Profile** panel to edit name/phone and change password.
- The product detail page has a **Reviews** section (average rating, a
  star-rating write form for signed-in customers, and the review list),
  plus **Related Products** (same category) and **Recently Viewed**
  (localStorage, works for guests too — also shown on the homepage once
  there's browsing history).
- The admin panel gained **Orders** and **Coupons** tabs next to the
  existing Products tab. Orders can have their status updated (auto-restocks
  on cancel); coupons can be created, disabled/enabled, and deleted.

## Setup

```bash
cd jeevani_backend
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5000** — that's your homepage. The admin
panel is at **http://localhost:5000/admin.html**, exactly like before.

The database (`jeevani.db`) and the default admin account are created
automatically on first run.

## Default admin login
- Username: `admin`
- Password: `alakhmehrotra@123`

(Same as before — but now it's stored as a hash, not plaintext.) Change it
any time via `POST /api/admin/change-password`, or directly in the database.

## Environment variables

```bash
export JEEVANI_SECRET_KEY="generate-a-long-random-string"
export JEEVANI_ADMIN_USER="your-username"
export JEEVANI_ADMIN_PASS="your-strong-password"
export FLASK_DEBUG=false
export PORT=5000

# Phase 3 — Razorpay (optional). Without these, the UPI/Card options tell
# the customer online payments aren't configured yet; Cash on Delivery
# always works.
export RAZORPAY_KEY_ID="rzp_live_or_test_xxxxxxxx"
export RAZORPAY_KEY_SECRET="your-razorpay-key-secret"

# Strongly recommended alongside Razorpay: without a webhook, an order only
# gets marked "paid" if the customer's browser is still open and online when
# payment completes. Add a webhook in the Razorpay dashboard pointing at
# POST /api/webhooks/razorpay (event: payment.captured), then set the
# secret it gives you here.
export RAZORPAY_WEBHOOK_SECRET="your-razorpay-webhook-secret"

# Only set this to "true" for local http://localhost development. Leave
# unset (or "false") in production so the session cookie requires HTTPS.
export JEEVANI_INSECURE_COOKIE=false

# Phase 4 — SMTP (optional but recommended). Without MAIL_SERVER set, order
# confirmation, shipping confirmation, and password-reset emails are simply
# skipped (logged to the console instead) — nothing else breaks.
export MAIL_SERVER="smtp.example.com"
export MAIL_PORT=587
export MAIL_USE_TLS=true          # set MAIL_USE_SSL=true instead if your provider uses port 465
export MAIL_USERNAME="apikey-or-username"
export MAIL_PASSWORD="your-smtp-password-or-api-key"
export MAIL_DEFAULT_SENDER="Shri Jeevani Sarees <orders@yourdomain.com>"

# Used to build the link inside password-reset emails (a background email
# thread has no request to infer this from). Set to your real domain in
# production, e.g. https://shrijeevanisarees.com
export SITE_URL="http://localhost:5000"
```
Set `JEEVANI_ADMIN_USER`/`JEEVANI_ADMIN_PASS` **before the first run** —
they only seed the initial admin account once.

## Security fixes applied in this pass
- Escaped all user-generated content (review names/comments, and — most
  importantly — customer checkout name/phone/order data shown on the admin
  Orders tab) before inserting it into the page. Previously this was stored
  XSS: a malicious checkout name or review could run arbitrary JavaScript
  in another visitor's browser, including the **admin's** authenticated
  session when they opened the Orders tab.
- `debug_mode` no longer defaults to `True` when `FLASK_DEBUG` is unset —
  it now defaults to `False`, so the Werkzeug debugger (remote code
  execution on any unhandled exception) isn't accidentally exposed in
  production.
- Added `SESSION_COOKIE_SECURE` so the session cookie is never sent over
  plain HTTP in production.
- Added basic rate limiting on `/api/admin/login` and `/api/auth/login` to
  slow down brute-force/credential-stuffing attempts.
- Closed a race condition where a capped coupon (`maxUses`) could be used
  slightly more than its limit under concurrent checkouts.
- Added a Razorpay webhook (`/api/webhooks/razorpay`) and a stale-order
  sweep so a payment that succeeds but never gets confirmed by the
  customer's browser (closed tab, lost connection) doesn't leave stock
  decremented and the order stuck "pending" forever — and so a customer
  can no longer lock up inventory by repeatedly starting checkout without
  paying.
- Fixed a bug where the "Place Order" button re-enabled itself immediately
  after the Razorpay widget opened, allowing a duplicate pending order (and
  a second stock decrement) to be created while the first payment was still
  in progress.

## API summary

| Method | Endpoint | Auth | Purpose |
|---|---|---|---|
| GET | `/api/products` | — | List products. Query params: `category`, `price`, `sort`, `q` |
| GET | `/api/products/<id>` | — | Single product |
| GET/POST | `/api/products/<id>/reviews` | GET public, POST customer | List/add reviews |
| POST | `/api/admin/login` | — | Admin login |
| POST | `/api/admin/logout` | — | Clears admin session |
| GET | `/api/admin/session` | — | `{loggedIn, username}` |
| GET/POST/PUT/DELETE | `/api/admin/products[/<id>]` | admin | Product CRUD |
| GET | `/api/admin/stats` | admin | Dashboard stat cards |
| POST | `/api/auth/signup` \| `/login` \| `/logout` | — | Customer accounts |
| GET | `/api/auth/session` | — | Current customer session |
| POST | `/api/auth/forgot-password` | — | Email a password reset link |
| POST | `/api/auth/reset-password` | — | Consume a reset token, set new password |
| PUT | `/api/auth/profile` | customer | Update name/phone (My Profile) |
| POST | `/api/auth/change-password` | customer | Change own password |
| GET/POST/PUT/DELETE | `/api/cart[/<id>]` | customer | Cart CRUD, `/merge`, `/clear` |
| GET/POST/DELETE | `/api/wishlist[/<id>]` | customer | Wishlist |
| POST | `/api/coupons/validate` | customer | Validate a coupon against the current cart |
| POST/GET | `/api/orders[/<order_number>]` | customer | Place a COD order / list or fetch orders |
| POST | `/api/orders/razorpay/create` \| `/verify` \| `/cancel` | customer | Razorpay checkout lifecycle |
| GET | `/api/admin/orders` | admin | All orders |
| PUT | `/api/admin/orders/<id>` | admin | Update order status (auto-restocks on cancel) |
| GET/POST/PUT/DELETE | `/api/admin/coupons[/<id>]` | admin | Coupon CRUD |
| GET | `/api/banners` | — | Active homepage banners |
| GET/POST/PUT/DELETE | `/api/admin/banners[/<id>]` | admin | Banner CRUD |
| POST | `/api/track/pageview` | — | Record a page/product view (analytics) |
| GET | `/api/admin/analytics` | admin | Revenue/orders/views time series, top products, order-status breakdown |
| GET | `/product/<slug>` | — | Product detail page with server-rendered SEO meta tags |
| GET | `/sitemap.xml` \| `/robots.txt` | — | SEO |

## Deployment note
The built-in `python app.py` server is for development. For production:
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:8000 app:app
```
and put it behind Nginx/Caddy with HTTPS.

