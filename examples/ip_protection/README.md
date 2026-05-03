# IP Protection Demo

End-to-end protection of a proprietary Python package. The vendor packs
once, ships the encrypted bundle to customers, and holds the decryption
key on a license server. A customer runs the algorithm only for as long
as their license is active; a captured bundle alone is entropy.

## Roles

| Script | Role | Has | Produces / does |
|---|---|---|---|
| `pack.py` | vendor dev | `proprietary/` source | `bundle.paker`, `release.key` |
| `license_server.py` | vendor ops | `release.key` | serves keys on POST `/key` |
| `client.py` | customer | `bundle.paker`, paker installed | fetches key, loads bundle, runs signal |
| `attack.py` | adversary | `bundle.paker` only | finds nothing recoverable |

## What's protected

[`proprietary/signal.py`](proprietary/signal.py) is the stand-in for any
IP-heavy Python code: coefficients, magic strings, identifier names, the
shape of the algorithm itself. The demo shows none of it can be recovered
from `bundle.paker` without a live key.

## Run the demo

```bash
./run_demo.sh
```

Orchestrates pack → license server (background) → client → attacker in
one terminal. Produces a transcript like:

```
>>> [1/4] pack.py — vendor packs the proprietary algorithm
[pack] wrote bundle.paker (7,294 bytes)
[pack] wrote release.key (32 bytes, keep on license server)

>>> [2/4] license_server.py — starting key server in background
[license] listening on http://127.0.0.1:9478/key

>>> [3/4] client.py — customer fetches the key and runs the signal
[client] machine_id = 'demo-machine-001'
[client] fetching key from http://127.0.0.1:9478/key ...
[client] got 32-byte key
[client] loaded bundle.paker (1 top-level packages)
[client] signal: {'action': 'HOLD', 'reason': 'z=0.31 within band', ...}
[client] session ended; key scrubbed from memory

>>> [4/4] attack.py — what a bundle-only attacker sees
==== ATTACK 1 — `strings` + grep for identifiers and coefficients ====
  miss   'PROPRIETARY_COEFFICIENTS'
  miss   'momentum_window'
  miss   'paker-demo-proprietary-alpha-v4'
  ...
==== ATTACK 3 — decrypt with a guessed key ====
  raised ValueError: HMAC verification failed — wrong key or tampered data
  → HMAC-SHA256 catches the wrong key before any plaintext is produced.
```

## Run each piece manually

Four terminals:

```bash
# Terminal 1 — pack once (vendor)
python pack.py

# Terminal 2 — license server (vendor ops)
python license_server.py

# Terminal 3 — customer
python client.py

# Terminal 4 — attacker (bundle-only, no key)
python attack.py
```

Deny a license: add an unknown `MACHINE_ID` and re-run the client:

```bash
MACHINE_ID=not-on-the-allowlist python client.py
# urllib.error.HTTPError: 403: not licensed
```

## What the attacker tries

`attack.py` runs five attack surfaces and prints the result of each:

1. **`strings` + grep** for identifier names, coefficients, magic strings.
   None survive; the bundle is ciphertext.
2. **Walk the JSON directly.** Every `code` field is base64-encoded
   AES-256-CTR output with an HMAC tag. Nothing human-readable.
3. **Decrypt with a guessed key.** HMAC-SHA256 verification rejects every
   wrong key before a single byte of plaintext is produced.
4. **Decompile with `uncompyle6` / `pycdc`.** The extracted bytes aren't a
   `.pyc` — no magic header, no valid structure. Decompilers refuse.
5. **`inspect.getsource`.** Can't happen without a module, can't get a
   module without a key.

## What paker does NOT protect against

`attack.py` ends with an honesty section about the runtime threat model.
Once a customer has the live key, they are running the code. Runtime
introspection (`sys.settrace`, `func.__code__.co_code` dumps, import-hook
injection) gives a determined attacker access to the loaded bytecode.

paker's defensive claim is **"bundle + live key is the attack bar."**
That bar is only as strong as license delivery:

- Per-session keys bound to device attestation raise the bar significantly.
- A license server that revokes, rate-limits, and rotates keys limits the
  blast radius of any single compromise.
- Static keys shipped with the binary (the PyArmor default) eliminate the
  bar entirely — paker explicitly rejects this design.

See [`docs/security.md`](../../docs/security.md) for the full threat model
analysis.

## Prerequisites

- Python ≥ 3.10
- `paker` installed (`pip install -e ../..` from this directory for dev,
  or `pip install paker` once published).
