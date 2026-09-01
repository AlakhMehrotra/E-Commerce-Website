"""
Standalone Shiprocket connection test.

Run this directly (no need to place a real order) to see the EXACT reason
Shiprocket orders aren't showing up:

    python test_shiprocket.py

It just tries to log in with your SHIPROCKET_EMAIL / SHIPROCKET_PASSWORD
and prints Shiprocket's own response. This is the same login call
_dispatch_to_shiprocket() makes internally, but here nothing is swallowed —
you'll see the real error.
"""
import os
from dotenv import load_dotenv
load_dotenv()
load_dotenv("a.env")

import requests

email = os.environ.get("SHIPROCKET_EMAIL", "")
password = os.environ.get("SHIPROCKET_PASSWORD", "")
pickup = os.environ.get("SHIPROCKET_PICKUP_LOCATION", "Primary")

print(f"SHIPROCKET_EMAIL           = {email!r}")
print(f"SHIPROCKET_PASSWORD        = {'*' * len(password) if password else '(empty)'}")
print(f"SHIPROCKET_PICKUP_LOCATION = {pickup!r}")
print()

if not email or not password:
    print("❌ SHIPROCKET_EMAIL or SHIPROCKET_PASSWORD is empty. Fix your .env first.")
    raise SystemExit(1)

print("Trying to log in to Shiprocket...")
try:
    res = requests.post(
        "https://apiv2.shiprocket.in/v1/external/auth/login",
        json={"email": email, "password": password},
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
        },
        timeout=15,
    )
    print(f"HTTP status: {res.status_code}")
    print(f"Response body: {res.text}")
    res.raise_for_status()
    token = res.json().get("token")
    if token:
        print("\n✅ Login succeeded! Token received.")
        print("   This means your SHIPROCKET_EMAIL/PASSWORD are correct and reachable.")
        print("   If orders still aren't showing up, the issue is more likely your")
        print(f"   SHIPROCKET_PICKUP_LOCATION ({pickup!r}) not matching a pickup address")
        print("   nickname exactly (case-sensitive) in your Shiprocket dashboard.")
    else:
        print("\n⚠️ Got a 200 response but no token in it — unexpected. See body above.")
except requests.exceptions.HTTPError:
    print("\n❌ Login failed. Common causes:")
    print("   - Wrong email or password")
    print("   - Shiprocket account requires a captcha/OTP step that blocks API login")
    print("   - Account not fully set up / KYC not submitted yet")
except requests.exceptions.RequestException as e:
    print(f"\n❌ Could not reach Shiprocket at all: {e}")
    print("   This usually means a network/firewall issue on this machine, not your")
    print("   credentials.")
