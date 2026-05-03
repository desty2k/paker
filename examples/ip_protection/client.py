"""Customer-side: fetch a per-session key, load the bundle, run the signal.

The customer has ``bundle.paker`` on disk (shipped with their install) and
paker installed. They do NOT have ``release.key``, ``proprietary/``, or any
way to decrypt the bundle offline. The license server is the only place
the key exists.
"""
from __future__ import annotations

import json
import os
import urllib.request

import paker

HERE = os.path.dirname(os.path.abspath(__file__))
LICENSE_URL = os.environ.get("LICENSE_URL", "http://127.0.0.1:9478/key")
MACHINE_ID = os.environ.get("MACHINE_ID", "demo-machine-001")


def fetch_key(machine_id: str) -> bytes:
    """Ask the license server for the decryption key. Returns 32 raw bytes."""
    body = json.dumps({"machine_id": machine_id}).encode()
    req = urllib.request.Request(
        LICENSE_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def main() -> None:
    print(f"[client] machine_id = {MACHINE_ID!r}")
    print(f"[client] fetching key from {LICENSE_URL} ...")
    key = fetch_key(MACHINE_ID)
    print(f"[client] got {len(key)}-byte key")

    with open(os.path.join(HERE, "bundle.paker")) as f:
        bundle = json.load(f)
    print(f"[client] loaded bundle.paker ({len(bundle)} top-level packages)")

    # Run the proprietary algorithm. Source never touches disk, arrives
    # decrypted in memory, disappears when the context exits and the key
    # bytearray is scrubbed.
    with paker.loads(bundle, key=key):
        from proprietary import recommend
        # Toy price series for the demo.
        prices = [100.0 + 0.5 * i + (i % 7 - 3) for i in range(40)]
        recommendation = recommend(prices)
        print(f"[client] signal: {recommendation}")

    print("[client] session ended; key scrubbed from memory")


if __name__ == "__main__":
    main()
