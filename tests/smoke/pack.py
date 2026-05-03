#!/usr/bin/env python3
"""Pack real-world packages into JSON bundles for smoke testing.

Run in a venv with the target packages installed:

    pip install paker psutil click pydantic requests pyyaml rich jinja2 \
                sqlalchemy numpy boto3 moto anthropic flask fastapi Pillow \
                mcp pypdfium2
    python tests/smoke/pack.py

Produces ``tests/smoke/bundles/<pkg>_plain.json`` and
``tests/smoke/bundles/<pkg>_enc.json`` for each package.
"""

from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLES_DIR = os.path.join(HERE, "bundles")
PAKER_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, PAKER_ROOT)

import paker

KEY = b"\x42" * 32

PACKAGES: dict[str, list[str]] = {
    # name  →  extra packages to include (undeclared companion deps)
    "click": [],
    "psutil": [],
    "jinja2": [],
    "pydantic": [],
    "requests": [],
    "yaml": [],
    "rich": [],
    "sqlalchemy": [],
    "numpy": [],
    "boto3": [],
    "anthropic": [],
    "flask": [],
    "fastapi": [],
    "PIL": [],
    "mcp": [],
    "pypdfium2": ["pypdfium2_raw", "pypdfium2_cfg"],
}


def pack_one(name: str, extras: list[str]) -> None:
    for variant, key in [("plain", None), ("enc", KEY)]:
        t0 = time.time()
        bundle = paker.dumps(name, key=key, include_deps=True)
        for extra in extras:
            bundle.update(paker.dumps(extra, key=key, include_deps=True))
        elapsed = time.time() - t0
        out = os.path.join(BUNDLES_DIR, f"{name}_{variant}.json")
        with open(out, "w") as f:
            json.dump(bundle, f)
        size = os.path.getsize(out)
        pkgs = len(bundle)
        print(f"  {name}_{variant}: {pkgs} pkgs, {size:,} bytes ({elapsed:.1f}s)")


def main() -> None:
    os.makedirs(BUNDLES_DIR, exist_ok=True)
    for name, extras in PACKAGES.items():
        try:
            pack_one(name, extras)
        except Exception as e:
            print(f"  {name}: SKIP ({type(e).__name__}: {e})")
    print("done")


if __name__ == "__main__":
    main()
