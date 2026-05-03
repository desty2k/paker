"""Load requests from an encrypted paker bundle and make HTTP calls.

Requires requests installed for the dump step: pip install requests
"""

import json
import os
import sys
import paker

KEY = os.urandom(32)

print("Dumping requests (encrypted)...")
bundle = paker.dumps("requests", key=KEY)
blob = json.dumps(bundle)
print(f"Bundle: {len(blob):,} bytes")
print(f"Source readable: {'def ' in blob[:1000]}\n")

for name in list(sys.modules):
    if name.startswith("requests"):
        del sys.modules[name]

with paker.loads(blob, key=KEY) as imp:
    import requests

    print(f"requests {requests.__version__} loaded from paker\n")

    r = requests.get("https://httpbin.org/get", params={"source": "paker"})
    print(f"GET https://httpbin.org/get")
    print(f"  Status: {r.status_code}")
    print(f"  Origin: {r.json().get('origin', 'unknown')}")

    r = requests.get("https://httpbin.org/user-agent")
    print(f"\nUser-Agent: {r.json()['user-agent']}")
