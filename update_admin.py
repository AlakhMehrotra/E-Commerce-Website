"""
One-off script: sets the admin portal username and password.

Safe to run whether jeevani.db already exists or not:
- If it doesn't exist yet, it creates it (same as starting app.py) and
  seeds the admin account with these credentials.
- If it already exists, it updates the existing admin row (or creates
  one if somehow none exists) without touching products/orders/etc.

Usage:
    python update_admin.py
"""
from werkzeug.security import generate_password_hash
from database import get_db, init_db

NEW_USERNAME = "alakhmehrotra"
NEW_PASSWORD = "alakhmehrotra@123"

init_db()  # creates tables + seeds defaults if the DB is brand new

conn = get_db()
existing = conn.execute("SELECT id FROM admin_users LIMIT 1").fetchone()
password_hash = generate_password_hash(NEW_PASSWORD)

if existing:
    conn.execute(
        "UPDATE admin_users SET username = ?, password_hash = ? WHERE id = ?",
        (NEW_USERNAME, password_hash, existing["id"]),
    )
else:
    conn.execute(
        "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
        (NEW_USERNAME, password_hash),
    )

conn.commit()
conn.close()

print(f"Admin credentials set: username='{NEW_USERNAME}', password='{NEW_PASSWORD}'")
