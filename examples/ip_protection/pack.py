"""Vendor-side: pack the proprietary algorithm into an encrypted bundle.

Run once, on a trusted machine with the source. Produces ``bundle.paker``
(the encrypted JSON blob) and ``release.key`` (the master key). The bundle
is shipped to customers; the master key is kept on the license server.
"""
import json
import os
import secrets
import sys

# Make the proprietary/ package importable regardless of cwd.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import paker


def main() -> None:
    master_key = secrets.token_bytes(32)

    bundle = paker.dumps("proprietary", key=master_key, include_deps=True)

    with open(os.path.join(HERE, "bundle.paker"), "w") as f:
        json.dump(bundle, f)

    # The master key never ships with the bundle. It lives on the license
    # server. Writing it to a file here is a demo convenience.
    with open(os.path.join(HERE, "release.key"), "wb") as f:
        f.write(master_key)

    size = os.path.getsize(os.path.join(HERE, "bundle.paker"))
    print(f"[pack] wrote bundle.paker ({size:,} bytes)")
    print(f"[pack] wrote release.key ({len(master_key)} bytes, keep on license server)")


if __name__ == "__main__":
    main()
