#!/usr/bin/env python3
"""
migrate_to_turso.py
-------------------
Migrates all existing products (and admin user + coupons) from local
jeevani.db into the Turso cloud database.

Usage:
    TURSO_DATABASE_URL="libsql://..." TURSO_AUTH_TOKEN="..." python3 migrate_to_turso.py
"""
import os, sys, sqlite3, json

TURSO_URL   = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

if not TURSO_URL or not TURSO_TOKEN:
    print("ERROR: Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN before running.")
    sys.exit(1)

try:
    import libsql_experimental as libsql
except ImportError:
    print("ERROR: Run: pip install libsql-experimental==0.0.18")
    sys.exit(1)

LOCAL_DB = os.path.join(os.path.dirname(__file__), "jeevani.db")
if not os.path.exists(LOCAL_DB):
    print(f"ERROR: Local database not found at {LOCAL_DB}")
    sys.exit(1)

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
with open(SCHEMA_PATH, "r") as f:
    schema_sql = f.read()

print(f"Connecting to Turso: {TURSO_URL}")
turso = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)

print("Creating tables in Turso...")
for stmt in schema_sql.split(";"):
    stmt = stmt.strip()
    if stmt:
        try:
            turso.execute(stmt)
        except Exception as e:
            if "already exists" not in str(e).lower():
                print(f"  Warning: {e}")
turso.commit()
print("  Tables ready.")

local = sqlite3.connect(LOCAL_DB)
local.row_factory = sqlite3.Row

local_products = local.execute("SELECT * FROM products").fetchall()
print(f"\nMigrating {len(local_products)} products...")
for p in local_products:
    try:
        cols = [c[1] for c in local.execute("PRAGMA table_info(products)").fetchall()]
        turso.execute("""
            INSERT OR REPLACE INTO products
            (id, name, category, price, badge, emoji, description, image_data,
             fabric, color, stock, featured, bestseller, new_arrival, slug,
             is_active, created_at, updated_at, images)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p["id"], p["name"], p["category"], p["price"],
            p["badge"] or "", p["emoji"] or "🌸", p["description"] or "",
            p["image_data"], p["fabric"] or "Pure Banarasi Silk",
            p["color"] or "", p["stock"] or 25,
            p["featured"] or 0, p["bestseller"] or 0, p["new_arrival"] or 0,
            p["slug"], p["is_active"] if "is_active" in cols else 1,
            p["created_at"] if "created_at" in cols else None,
            p["updated_at"] if "updated_at" in cols else None,
            p["images"] if "images" in cols else "[]",
        ))
        print(f"  ok {p['name'][:50]}")
    except Exception as e:
        print(f"  ERR {p['name'][:50]} -- {e}")
turso.commit()

admin_users = local.execute("SELECT * FROM admin_users").fetchall()
print(f"\nMigrating {len(admin_users)} admin users...")
for u in admin_users:
    try:
        turso.execute(
            "INSERT OR REPLACE INTO admin_users (id, username, password_hash) VALUES (?,?,?)",
            (u["id"], u["username"], u["password_hash"])
        )
        print(f"  ok admin: {u['username']}")
    except Exception as e:
        print(f"  ERR {e}")
turso.commit()

try:
    coupons = local.execute("SELECT * FROM coupons").fetchall()
    print(f"\nMigrating {len(coupons)} coupons...")
    for c in coupons:
        try:
            turso.execute("""
                INSERT OR REPLACE INTO coupons
                (id, code, discount_type, discount_value, min_order, max_uses, uses_count, is_active)
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                c["id"], c["code"], c["discount_type"], c["discount_value"],
                c["min_order"] or 0, c["max_uses"], c["uses_count"] or 0, 1
            ))
            print(f"  ok coupon: {c['code']}")
        except Exception as e:
            print(f"  ERR {c['code']} -- {e}")
    turso.commit()
except Exception as e:
    print(f"  Coupons skipped: {e}")

local.close()
print("\nMigration complete! Your Turso database is ready.")
print(f"   Products migrated: {len(local_products)}")
