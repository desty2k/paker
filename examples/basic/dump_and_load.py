"""Basic example: dump a package to JSON and load it back."""

import json
import paker

# Create a simple module inline
modules = {
    "greeting": {
        "type": "module",
        "extension": "py",
        "code": (
            "def hello(name):\n"
            "    return f'Hello, {name}!'\n"
            "\n"
            "def goodbye(name):\n"
            "    return f'Goodbye, {name}!'\n"
        ),
    }
}

# Load from dict
with paker.loads(modules) as imp:
    import greeting

    print(greeting.hello("world"))
    print(greeting.goodbye("world"))
# greeting is unloaded here


# Dump a real installed package to JSON file
# (requires 'requests' to be installed: pip install requests)
try:
    with open("/tmp/requests_bundle.json", "w") as f:
        paker.dump("requests", f, indent=2)
    print(f"\nDumped requests to /tmp/requests_bundle.json")

    # Load it back
    import sys

    for name in list(sys.modules):
        if name.startswith("requests"):
            del sys.modules[name]

    with open("/tmp/requests_bundle.json", "r") as f:
        with paker.load(f) as imp:
            import requests

            print(f"requests {requests.__version__} loaded from JSON")
except ImportError:
    print("\nSkipping requests example (not installed)")
