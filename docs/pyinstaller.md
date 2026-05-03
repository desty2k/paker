# Using paker with PyInstaller

paker includes a PyInstaller hook that automatically bundles the C extension
and all required submodules. This guide covers building standalone binaries
that can load Python modules from encrypted paker bundles at runtime.

## Basic usage

Install paker (not as editable — PyInstaller needs a proper install):

```bash
pip install paker
pip install pyinstaller
```

Build your script:

```bash
pyinstaller --onefile your_script.py
```

The paker hook (`hook-paker.py`) is registered via the `pyinstaller40` entry
point and is picked up automatically. It includes:

- All paker submodules (`paker._loaders`, `paker._crypto`, etc.)
- The `_paker_crypto` C extension (AES-256 decryption + memory scrubbing)
- Standard library modules used by paker (`pkgutil`, `uuid`, `hashlib`, etc.)

## What works in a PyInstaller binary

| Feature | Status |
|---|---|
| `paker.loads()` — load modules from dict/JSON | Works |
| `paker.load()` — load from file | Works |
| Encrypted modules (`key=...`) | Works |
| C-level decryption (`_paker_crypto`) | Works |
| `paker.dumps()` / `paker.dump()` | Works (but requires source packages installed) |
| Platform-specific native loaders | Works (tempfile/unlink on macOS, memfd on Linux) |

## Example: client that loads libraries from a server

This pattern is useful for deploying a lightweight client binary that receives
its AI/ML libraries at runtime from a server.

**Client script** (`client.py`):

```python
import json
import socket
import paker

def main():
    # Connect to server, receive encrypted bundle
    sock = socket.socket()
    sock.connect(("server.example.com", 9477))
    
    bundle_size = int(sock.recv(1024).decode().strip())
    bundle_data = b""
    while len(bundle_data) < bundle_size:
        bundle_data += sock.recv(65536)
    
    bundle = json.loads(bundle_data)
    key = bundle["key"].encode()
    
    # Load all libraries from memory — no pip install needed
    imp = paker.loads(bundle["modules"], key=key)
    
    # Now use libraries that were delivered over the wire
    import anthropic
    client = anthropic.Anthropic(api_key=bundle["api_key"])
    # ...

if __name__ == "__main__":
    main()
```

**Build it:**

```bash
pip install paker
pyinstaller --onefile client.py
```

The resulting binary (`dist/client`) is ~10 MB and contains only paker + stdlib.
All application libraries arrive encrypted at runtime.

## Troubleshooting

### `ModuleNotFoundError: No module named 'paker'`

paker must be installed as a regular package, not editable (`pip install -e`).
PyInstaller cannot bundle editable installs:

```bash
pip install paker          # correct
pip install -e ./paker     # won't work with PyInstaller
```

### `ModuleNotFoundError` for stdlib modules

If you see missing stdlib modules like `uuid`, `pkgutil`, or `hashlib`, add them
as hidden imports:

```bash
pyinstaller --onefile --hidden-import=uuid --hidden-import=pkgutil your_script.py
```

This should be rare — the paker hook includes these automatically.

### `_paker_crypto` not found

The C extension must be compiled for your platform. If you installed paker from
a wheel, it should be included. If you installed from source:

```bash
pip install paker --no-binary :all:
```

This compiles the C extension during install. Requires a C compiler (gcc/clang/MSVC).

## Binary size

| Component | Size |
|---|---|
| Minimal paker binary (stdlib + paker) | ~10 MB |
| With anthropic SDK bundled at build time | ~25 MB |
| SDK delivered at runtime (not in binary) | ~10 MB binary + ~14 MB network |
