"""Bundle a package with all its transitive dependencies in one call."""

import json
import os
import sys
import paker

KEY = os.urandom(32)

# Dump httpx with all its dependencies auto-discovered
# (requires 'httpx' to be installed: pip install httpx)
try:
    import httpx as _  # noqa: F401

    print("Discovering httpx dependencies...")
    from paker.deps import find_dependencies

    deps = find_dependencies("httpx")
    print(f"  Found {len(deps)} packages: {deps}")

    print("\nBundling with include_deps=True...")
    bundle = paker.dumps("httpx", key=KEY, include_deps=True)
    blob = json.dumps(bundle)
    print(f"  Packages in bundle: {sorted(bundle.keys())}")
    print(f"  Total size: {len(blob):,} bytes")

    # Clear and reload from the bundle
    for name in list(sys.modules):
        for pkg in bundle:
            if name == pkg or name.startswith(pkg + "."):
                del sys.modules[name]
                break

    with paker.loads(blob, key=KEY) as imp:
        import httpx

        print(f"\n  httpx loaded from paker: {httpx.Client is not None}")

except ImportError:
    print("Skipping (httpx not installed: pip install httpx)")
