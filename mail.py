"""
Phase 4 CHANGE: Transactional email — order confirmation, shipping
confirmation, and password reset — via SMTP using Flask-Mail.

Design notes:
- All sending happens on a background thread so a slow/unreachable SMTP
  server never adds latency to (or breaks) the checkout/admin/auth request
  that triggered it. Flask-Mail's Message send is wrapped in
  `mail.connect()` + app context, since the sending thread has no request
  context of its own.
- If SMTP isn't configured (no MAIL_SERVER), sending is a no-op that logs
  to the console instead of raising — consistent with how Razorpay quietly
  degrades to "not configured" elsewhere in this project rather than
  crashing checkout.
- Every email has both an HTML and a plain-text part. All dynamic values
  are escaped with html.escape() before going into the HTML part — this is
  server-rendered HTML (unlike the frontend), so it needs its own escaping,
  same principle as the stored-XSS fix already applied to the Admin Orders
  tab.
"""
import os
import html
import threading
import logging

from flask import current_app
from flask_mail import Mail, Message

logger = logging.getLogger("jeevani.mail")

mail = Mail()

MAIL_ENABLED = bool(os.environ.get("MAIL_SERVER"))

STORE_NAME = "Shri Jeevani Sarees"
BRAND_COLOR = "#8b1e3f"   # matches the site's crimson/maroon accent
CREAM = "#faf6f0"


def init_mail(app):
    """Call once from app.py after creating the Flask app."""
    app.config.setdefault("MAIL_SERVER", os.environ.get("MAIL_SERVER", ""))
    app.config.setdefault("MAIL_PORT", int(os.environ.get("MAIL_PORT", "587")))
    app.config.setdefault("MAIL_USE_TLS", os.environ.get("MAIL_USE_TLS", "true").lower() == "true")
    app.config.setdefault("MAIL_USE_SSL", os.environ.get("MAIL_USE_SSL", "false").lower() == "true")
    app.config.setdefault("MAIL_USERNAME", os.environ.get("MAIL_USERNAME", ""))
    app.config.setdefault("MAIL_PASSWORD", os.environ.get("MAIL_PASSWORD", ""))
    app.config.setdefault(
        "MAIL_DEFAULT_SENDER",
        os.environ.get("MAIL_DEFAULT_SENDER", f"{STORE_NAME} <no-reply@shrijeevanisarees.example>"),
    )
    # Used to build absolute links (password reset) inside emails, since a
    # background thread has no request context to infer the host from.
    app.config.setdefault("SITE_URL", os.environ.get("SITE_URL", "http://localhost:5000"))
    # Phase 6: where contact-form submissions get emailed. Falls back to the
    # sender address itself (store owner's inbox) if not set separately, so
    # contact notifications work with zero extra configuration once SMTP is
    # already set up for order emails.
    app.config.setdefault(
        "CONTACT_NOTIFY_EMAIL",
        os.environ.get("CONTACT_NOTIFY_EMAIL", app.config.get("MAIL_USERNAME", "")),
    )
    mail.init_app(app)
    if not MAIL_ENABLED:
        logger.warning(
            "MAIL_SERVER is not set — emails will be logged to the console instead of sent. "
            "See README.md for SMTP setup."
        )


def _send_async(app, msg):
    with app.app_context():
        try:
            mail.send(msg)
        except Exception as exc:  # noqa: BLE001 — never let email failure surface to the customer
            logger.error("Failed to send email to %s: %s", msg.recipients, exc)


def _dispatch(subject, to, html_body, text_body):
    """Send in the background if SMTP is configured; otherwise log."""
    if not to:
        return
    if not MAIL_ENABLED:
        logger.info("[mail disabled] Would send %r to %s", subject, to)
        return

    app = current_app._get_current_object()
    msg = Message(subject=subject, recipients=[to], html=html_body, body=text_body)
    thread = threading.Thread(target=_send_async, args=(app, msg), daemon=True)
    thread.start()


def _wrap_html(inner_html, preheader=""):
    """Shared shell so every email looks consistent with the storefront."""
    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0; padding:0; background:{CREAM}; font-family:Georgia,'Times New Roman',serif; color:#2b2320;">
  <span style="display:none; max-height:0; overflow:hidden;">{html.escape(preheader)}</span>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{CREAM}; padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0"
             style="background:#ffffff; border-radius:8px; overflow:hidden; border:1px solid #e7ddd0;">
        <tr>
          <td style="background:{BRAND_COLOR}; padding:22px 28px;">
            <span style="color:#fff; font-size:20px; letter-spacing:0.5px;">{STORE_NAME}</span>
          </td>
        </tr>
        <tr><td style="padding:28px;">
          {inner_html}
        </td></tr>
        <tr>
          <td style="padding:18px 28px; background:{CREAM}; font-size:12px; color:#8a7f70; border-top:1px solid #e7ddd0;">
            This is an automated message from {STORE_NAME}. If you weren't expecting it, you can safely ignore it.
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _money(paise_or_rupees):
    """Amounts in this project are stored as whole rupees already (see
    database.py / schema.sql — INTEGER columns, no paise conversion for
    display), so just format with thousands separators."""
    try:
        return f"₹{int(paise_or_rupees):,}"
    except (TypeError, ValueError):
        return f"₹{paise_or_rupees}"


# ─────────────────────────────────────────────────────────────────────────
# Order confirmation
# ─────────────────────────────────────────────────────────────────────────
def send_order_confirmation_email(to_email, order, items):
    """order: dict shaped like database.row_to_order(). items: list shaped
    like database.row_to_order_item()."""
    if not to_email:
        return

    rows = "".join(
        f"""<tr>
              <td style="padding:8px 0; border-bottom:1px solid #f0e9df;">{html.escape(str(i['name']))} × {int(i['quantity'])}</td>
              <td style="padding:8px 0; border-bottom:1px solid #f0e9df; text-align:right;">{_money(i['price'] * i['quantity'])}</td>
            </tr>"""
        for i in items
    )

    summary_rows = f"""
      <tr><td style="padding:4px 0;">Subtotal</td><td style="padding:4px 0; text-align:right;">{_money(order['subtotal'])}</td></tr>
      <tr><td style="padding:4px 0;">Tax</td><td style="padding:4px 0; text-align:right;">{_money(order['tax'])}</td></tr>
      <tr><td style="padding:4px 0;">Shipping</td><td style="padding:4px 0; text-align:right;">{'Free' if order['shipping'] == 0 else _money(order['shipping'])}</td></tr>
      {f'<tr><td style="padding:4px 0;">Discount ({html.escape(order["couponCode"])})</td><td style="padding:4px 0; text-align:right;">-{_money(order["discount"])}</td></tr>' if order.get('discount') else ''}
      <tr><td style="padding:8px 0 0; font-weight:bold;">Total</td><td style="padding:8px 0 0; text-align:right; font-weight:bold;">{_money(order['total'])}</td></tr>
    """

    payment_note = (
        "Payment: Cash on Delivery — pay when your order arrives."
        if order["paymentMethod"] == "cod"
        else "Payment received — thank you!"
    )

    inner = f"""
      <h2 style="margin-top:0; color:{BRAND_COLOR};">Your order is confirmed</h2>
      <p>Namaste {html.escape(order['customerName'])},</p>
      <p>Thank you for shopping with us. We've received your order and will begin preparing it right away.</p>
      <p style="background:{CREAM}; padding:10px 14px; border-radius:6px;">
        <strong>Order #{html.escape(order['orderNumber'])}</strong><br>
        {html.escape(payment_note)}
      </p>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:12px;">
        {rows}
      </table>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:10px; border-top:1px solid #e7ddd0; padding-top:6px;">
        {summary_rows}
      </table>
      <h3 style="color:{BRAND_COLOR}; margin-bottom:6px;">Shipping to</h3>
      <p style="margin-top:0;">
        {html.escape(order['customerName'])}<br>
        {html.escape(order['customerAddress'])}<br>
        {html.escape(order['customerPincode'])}<br>
        Phone: {html.escape(order['customerPhone'])}
      </p>
      <p>You can track this order any time from <strong>My Orders</strong> after signing in.</p>
    """

    text = (
        f"Your order is confirmed — Order #{order['orderNumber']}\n\n"
        f"Namaste {order['customerName']},\n\n"
        f"Thank you for shopping with us. {payment_note}\n\n"
        + "\n".join(f"{i['name']} x{i['quantity']} - {_money(i['price'] * i['quantity'])}" for i in items)
        + f"\n\nTotal: {_money(order['total'])}\n\n"
        f"Shipping to:\n{order['customerName']}\n{order['customerAddress']}\n{order['customerPincode']}\nPhone: {order['customerPhone']}\n"
    )

    _dispatch(
        subject=f"Order confirmed — #{order['orderNumber']}",
        to=to_email,
        html_body=_wrap_html(inner, preheader=f"Your order #{order['orderNumber']} is confirmed."),
        text_body=text,
    )


# ─────────────────────────────────────────────────────────────────────────
# Shipping confirmation
# ─────────────────────────────────────────────────────────────────────────
def send_shipping_confirmation_email(to_email, order):
    if not to_email:
        return

    inner = f"""
      <h2 style="margin-top:0; color:{BRAND_COLOR};">Your order has shipped</h2>
      <p>Namaste {html.escape(order['customerName'])},</p>
      <p>Good news — your order is on its way.</p>
      <p style="background:{CREAM}; padding:10px 14px; border-radius:6px;">
        <strong>Order #{html.escape(order['orderNumber'])}</strong><br>
        Total: {_money(order['total'])}
      </p>
      <h3 style="color:{BRAND_COLOR}; margin-bottom:6px;">Shipping to</h3>
      <p style="margin-top:0;">
        {html.escape(order['customerName'])}<br>
        {html.escape(order['customerAddress'])}<br>
        {html.escape(order['customerPincode'])}<br>
        Phone: {html.escape(order['customerPhone'])}
      </p>
      <p>You can follow its progress any time from <strong>My Orders</strong> after signing in.</p>
    """

    text = (
        f"Your order has shipped — Order #{order['orderNumber']}\n\n"
        f"Namaste {order['customerName']},\n\n"
        f"Good news — your order is on its way.\n\n"
        f"Total: {_money(order['total'])}\n\n"
        f"Shipping to:\n{order['customerName']}\n{order['customerAddress']}\n{order['customerPincode']}\nPhone: {order['customerPhone']}\n"
    )

    _dispatch(
        subject=f"Your order has shipped — #{order['orderNumber']}",
        to=to_email,
        html_body=_wrap_html(inner, preheader=f"Order #{order['orderNumber']} is on its way."),
        text_body=text,
    )


# ─────────────────────────────────────────────────────────────────────────
# Password reset
# ─────────────────────────────────────────────────────────────────────────
def send_password_reset_email(to_email, name, reset_url, expires_minutes):
    if not to_email:
        return

    inner = f"""
      <h2 style="margin-top:0; color:{BRAND_COLOR};">Reset your password</h2>
      <p>Namaste {html.escape(name)},</p>
      <p>We received a request to reset the password on your {STORE_NAME} account. Click the button below to choose a new one:</p>
      <p style="text-align:center; margin:26px 0;">
        <a href="{html.escape(reset_url)}"
           style="background:{BRAND_COLOR}; color:#fff; text-decoration:none; padding:12px 26px; border-radius:6px; display:inline-block;">
           Reset Password
        </a>
      </p>
      <p style="font-size:13px; color:#8a7f70;">This link expires in {expires_minutes} minutes. If you didn't request this, you can safely ignore this email — your password won't change.</p>
      <p style="font-size:13px; color:#8a7f70; word-break:break-all;">Or copy this link: {html.escape(reset_url)}</p>
    """

    text = (
        f"Reset your password\n\n"
        f"Namaste {name},\n\n"
        f"We received a request to reset the password on your {STORE_NAME} account.\n"
        f"Open this link to choose a new one (expires in {expires_minutes} minutes):\n{reset_url}\n\n"
        f"If you didn't request this, you can safely ignore this email.\n"
    )

    _dispatch(
        subject=f"Reset your {STORE_NAME} password",
        to=to_email,
        html_body=_wrap_html(inner, preheader="Reset your password."),
        text_body=text,
    )


# ─────────────────────────────────────────────────────────────────────────
# Phase 6: Contact form notification (to the store, not the customer)
# ─────────────────────────────────────────────────────────────────────────
def send_contact_notification_email(admin_email, contact):
    """contact: dict shaped like database.row_to_contact_message(). Sent to
    the store's inbox (CONTACT_NOTIFY_EMAIL) so a submission is never missed
    even though it's also saved to the DB for the admin panel."""
    if not admin_email:
        logger.info("[mail] CONTACT_NOTIFY_EMAIL not set — contact message only saved to DB, not emailed.")
        return

    subject_line = contact.get("subject") or "(no subject)"
    inner = f"""
      <h2 style="margin-top:0; color:{BRAND_COLOR};">New contact form message</h2>
      <p style="background:{CREAM}; padding:10px 14px; border-radius:6px;">
        <strong>{html.escape(contact['name'])}</strong><br>
        {html.escape(contact['email'])}{f" · {html.escape(contact['phone'])}" if contact.get('phone') else ""}
      </p>
      <p><strong>Subject:</strong> {html.escape(subject_line)}</p>
      <p style="white-space:pre-wrap;">{html.escape(contact['message'])}</p>
      <p style="font-size:13px; color:#8a7f70;">Reply directly to this sender's email address, or view it in the admin panel's Contact tab.</p>
    """

    text = (
        f"New contact form message\n\n"
        f"From: {contact['name']} <{contact['email']}>\n"
        + (f"Phone: {contact['phone']}\n" if contact.get("phone") else "")
        + f"Subject: {subject_line}\n\n{contact['message']}\n"
    )

    _dispatch(
        subject=f"New enquiry: {subject_line}",
        to=admin_email,
        html_body=_wrap_html(inner, preheader=f"New message from {contact['name']}"),
        text_body=text,
    )


# ─────────────────────────────────────────────────────────────────────────
# Phase 6: Newsletter welcome email
# ─────────────────────────────────────────────────────────────────────────
def send_newsletter_welcome_email(to_email, unsubscribe_url):
    if not to_email:
        return

    inner = f"""
      <h2 style="margin-top:0; color:{BRAND_COLOR};">You're on the list!</h2>
      <p>Namaste,</p>
      <p>Thank you for subscribing to the {STORE_NAME} newsletter. You'll be the first to hear about
      new arrivals, festive collections, and members-only offers.</p>
      <p style="font-size:13px; color:#8a7f70; margin-top:26px;">
        Didn't mean to subscribe? <a href="{html.escape(unsubscribe_url)}" style="color:{BRAND_COLOR};">Unsubscribe here</a>.
      </p>
    """

    text = (
        f"You're on the list!\n\n"
        f"Thank you for subscribing to the {STORE_NAME} newsletter. You'll be the first to hear "
        f"about new arrivals, festive collections, and members-only offers.\n\n"
        f"Didn't mean to subscribe? Unsubscribe here: {unsubscribe_url}\n"
    )

    _dispatch(
        subject=f"Welcome to the {STORE_NAME} newsletter",
        to=to_email,
        html_body=_wrap_html(inner, preheader="You're subscribed!"),
        text_body=text,
    )
