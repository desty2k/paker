"""Load pydantic from an encrypted bundle and use data models.

Requires pydantic installed for the dump step: pip install pydantic
"""

import json
import os
import sys
import paker

KEY = os.urandom(32)

print("Dumping pydantic with all deps (encrypted)...")
bundle = paker.dumps("pydantic", key=KEY, include_deps=True)
blob = json.dumps(bundle)
print(f"Packages: {sorted(bundle.keys())}")
print(f"Bundle: {len(blob):,} bytes\n")

for name in list(sys.modules):
    for pkg in bundle:
        if name == pkg or name.startswith(pkg + "."):
            del sys.modules[name]
            break

with paker.loads(blob, key=KEY) as imp:
    from pydantic import BaseModel

    class User(BaseModel):
        name: str
        age: int
        email: str

    user = User(name="Alice", age=30, email="alice@example.com")
    print(f"User: {user}")
    print(f"JSON: {user.model_dump_json()}")

    try:
        bad = User(name="Bob", age="not a number", email="bob@test.com")
    except Exception as e:
        print(f"\nValidation error (expected): {type(e).__name__}")
