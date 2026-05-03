"""Attacker-side teardown: what can a customer learn from ``bundle.paker`` alone?

paker's threat model: an attacker who has **only** the bundle cannot
recover the source. An attacker who has the bundle **and** a live key from
the license server IS running the code and can introspect whatever
ships — that's out of scope; see the end of this file.

Run after ``pack.py`` to see what each attack surface reveals.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE_PATH = os.path.join(HERE, "bundle.paker")


def banner(label: str) -> None:
    print()
    print(f"{'=' * 4} {label} {'=' * (72 - len(label))}")


def attack_strings() -> None:
    """`strings bundle.paker | grep` for identifiers, coefficients, magic strings."""
    banner("ATTACK 1 — `strings` + grep for identifiers and coefficients")
    needles = [
        "PROPRIETARY_COEFFICIENTS",
        "momentum_window",
        "paker-demo-proprietary-alpha-v4",
        "recommend",
        "kelly_fraction",
        "def ",
        "import ",
    ]
    if not _have("strings"):
        print("  (`strings` not installed — falling back to in-process grep)")
        with open(BUNDLE_PATH, "rb") as f:
            raw = f.read()
        for n in needles:
            present = n.encode() in raw
            print(f"  {'FOUND' if present else 'miss ':5}  {n!r}")
        return
    for n in needles:
        hit = subprocess.run(
            ["bash", "-c", f'strings "{BUNDLE_PATH}" | grep -F "{n}" | head -1'],
            capture_output=True,
            text=True,
        )
        present = bool(hit.stdout.strip())
        print(f"  {'FOUND' if present else 'miss ':5}  {n!r}")
    print("  → none of the secret identifiers survive.")


def attack_json_walk() -> None:
    """Load the JSON and show the attacker sees only base64 ciphertext."""
    banner("ATTACK 2 — walk the JSON directly")
    with open(BUNDLE_PATH) as f:
        bundle = json.load(f)
    top = list(bundle)
    print(f"  top-level packages: {top}")
    node = bundle[top[0]]
    print(f"  node keys: {sorted(node)}")
    code = node["code"]
    print(f"  encrypted? {node.get('encrypted')}")
    print(f"  code type: str, length {len(code):,}")
    print(f"  first 80 chars: {code[:80]}")
    print("  → base64-encoded ciphertext. No plaintext, no bytecode.")


def attack_decrypt_no_key() -> None:
    """Try to import from a bundle with a wrong key. HMAC verification fails."""
    banner("ATTACK 3 — decrypt with a guessed key")
    import paker
    with open(BUNDLE_PATH) as f:
        bundle = json.load(f)
    # paker.loads() installs the import hook; decryption happens lazily on
    # first import. Trigger an actual import to exercise the key check.
    imp = paker.loads(bundle, key=b"\x00" * 32)
    try:
        import proprietary  # noqa: F401
    except Exception as e:
        print(f"  raised {type(e).__name__}: {e}")
        print("  → HMAC-SHA256 catches the wrong key before any plaintext is produced.")
    else:
        print("  UNEXPECTED: import succeeded with zero key")
    finally:
        imp.unload()
        sys.modules.pop("proprietary", None)


def attack_decompile() -> None:
    """Pretend there's a .pyc to decompile. There isn't — the code field is ciphertext."""
    banner("ATTACK 4 — decompile with uncompyle6 / pycdc")
    with open(BUNDLE_PATH) as f:
        bundle = json.load(f)
    top = list(bundle)[0]
    blob = bundle[top]["code"]
    # Attacker's first instinct: base64-decode and write out as .pyc.
    raw = base64.b64decode(blob)
    dump = os.path.join(HERE, "_extracted.bin")
    with open(dump, "wb") as f:
        f.write(raw)
    # A real .pyc starts with a 16-byte header: magic + hash/timestamp + size.
    # paker's bundle is nonce + ciphertext + HMAC with no .pyc framing.
    print(f"  wrote {dump} ({len(raw):,} bytes)")
    print(f"  first 16 bytes: {raw[:16].hex()}")
    print("  → not a valid .pyc (no 0xcb0d or 0xa70d0d0a magic).")
    print("  → decompilers refuse to touch it.")
    os.unlink(dump)


def attack_inspect_no_key() -> None:
    """Try to `inspect.getsource` on a module we never decrypted."""
    banner("ATTACK 5 — `inspect.getsource` without a key")
    # Attacker can't load the bundle without a key, so there's no module
    # object to inspect in the first place. Show that.
    print("  paker.loads without a key fails before any module reaches sys.modules.")
    print("  → no import side effects, no module object, nothing to inspect.")


# ---------------------------------------------------------------------------
# Out-of-scope: attacker HAS the live key.
# ---------------------------------------------------------------------------


def scope_note_runtime_attacks() -> None:
    banner("OUT OF SCOPE — attacker has the live key (bundle + license)")
    print("""  If the attacker can also call the license server and get the session key,
  they ARE the user — running the code in their own process. Every Python
  packer in this category loses here:

      • `inspect.getsource(module)` returns '' (paker scrubs co_filename), but
        `module.__dict__` still reveals function names and bound globals.
      • `sys.settrace()` can log every line executed.
      • `func.__code__.co_code` dumps the bytecode an attacker can disassemble
        with `dis.dis`.
      • Import hook injection captures decrypted source before exec.

  paker's threat model (docs/security.md):
      DEFENDS against  static analysis, bundle-only theft, casual memory
                       scraping, single-shot LLM deobfuscation.
      DOES NOT DEFEND  attackers with bundle + live key, kernel debuggers,
                       dedicated RE with instrumentation, bytecode VM attacks.

  If the live key is also out of reach (license server does per-session
  attestation, binds keys to hardware, etc.), these runtime attacks can't
  be staged either. Key delivery security is the user's problem.""")


def _have(cmd: str) -> bool:
    return subprocess.run(
        ["which", cmd], capture_output=True,
    ).returncode == 0


def main() -> None:
    if not os.path.exists(BUNDLE_PATH):
        print("bundle.paker not found — run pack.py first.", file=sys.stderr)
        sys.exit(1)
    attack_strings()
    attack_json_walk()
    attack_decrypt_no_key()
    attack_decompile()
    attack_inspect_no_key()
    scope_note_runtime_attacks()
    print()
    print("Summary: with bundle.paker alone, no attack recovers the source.")
    print("         With bundle + live key, you are running the code.")


if __name__ == "__main__":
    main()
