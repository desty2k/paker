"""Encrypt modules with AES-256 so they can't be read without the key."""

import json
import os
import sys
import paker
from paker._crypto import encrypt

KEY = os.urandom(32)

# A module with sensitive logic — encrypt each code value
source = (
    "MARGIN = 0.15\n"
    "DISCOUNT_TIERS = {100: 0.05, 500: 0.10, 1000: 0.15}\n"
    "\n"
    "def calculate(quantity, unit_price):\n"
    "    subtotal = quantity * unit_price\n"
    "    discount = 0\n"
    "    for threshold, rate in sorted(DISCOUNT_TIERS.items(), reverse=True):\n"
    "        if quantity >= threshold:\n"
    "            discount = rate\n"
    "            break\n"
    "    return round(subtotal * (1 - discount) * (1 + MARGIN), 2)\n"
)

modules = {
    "pricing": {
        "type": "module",
        "extension": "py",
        "encrypted": True,
        "code": encrypt(source, KEY),
    }
}

blob = json.dumps(modules)

# The JSON is not readable
print("Encrypted JSON (first 100 chars):")
print(blob[:100] + "...")
print(f"Total size: {len(blob):,} bytes")

# Load with the correct key
print("\nLoading with correct key:")
with paker.loads(modules, key=KEY) as imp:
    import pricing

    print(f"  calculate(50, 10.0)   = {pricing.calculate(50, 10.0)}")
    print(f"  calculate(200, 10.0)  = {pricing.calculate(200, 10.0)}")
    print(f"  calculate(1000, 10.0) = {pricing.calculate(1000, 10.0)}")

# Wrong key fails
print("\nLoading with wrong key:")
try:
    wrong_key = os.urandom(32)
    with paker.loads(blob, key=wrong_key) as imp:
        import pricing  # noqa: F811
except ValueError as e:
    print(f"  {e}")


# You can also encrypt a real installed package with dumps():
print("\n--- Encrypting an installed package ---")
try:
    encrypted_bundle = paker.dumps("json", key=KEY)
    print(f"Encrypted 'json' module: {len(json.dumps(encrypted_bundle)):,} bytes")
    print(f"Source readable: {'def ' in str(encrypted_bundle)[:500]}")
except Exception as e:
    print(f"  {e}")
