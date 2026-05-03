# paker

[![PyPI](https://img.shields.io/pypi/v/paker)](https://pypi.org/project/paker/)
[![Python](https://img.shields.io/pypi/pyversions/paker)](https://pypi.org/project/paker/)
[![License](https://img.shields.io/github/license/desty2k/paker)](LICENSE)
[![Build](https://github.com/desty2k/paker/actions/workflows/build.yml/badge.svg)](https://github.com/desty2k/paker/actions/workflows/build.yml)
[![codecov](https://codecov.io/gh/desty2k/paker/branch/master/graph/badge.svg)](https://codecov.io/gh/desty2k/paker)

Import Python packages — including native C extensions — from encrypted JSON
documents. Entirely in memory. Zero disk footprint.<sup>1</sup>

<sub>1. Zero disk on Linux (memfd) and Windows (MemoryModule) for most extensions.
macOS uses write→dlopen→unlink (file exists for milliseconds). Windows
extensions with Static TLS (Rust, some C++) require a tempfile. See
`disk_policy` for control over disk behavior.</sub>

## Features

- **In-memory module loading** — load `.py`, `.pyc`, `.so`, `.pyd`, and `.dll`
  modules from JSON without writing to disk
- **AES-256 encryption** — modules are encrypted at rest, decrypted in C-level
  buffers that never touch the Python heap
- **Key separated from payload** — the decryption key is delivered at runtime
  from your own channel (license server, user secret, HSM). A captured bundle
  is entropy without a live key
- **Cross-platform native loaders** — `memfd_create` on Linux, `write→dlopen→unlink`
  on macOS, in-memory PE loading on Windows
- **Bundled native deps** — packages like Pillow and numpy that ship
  `.dylibs/` / `.libs/` alongside their extensions work end-to-end
- **Resource files** — `importlib.resources` reads from the bundle, including
  memory-backed paths for C-level `fopen` callers (OpenSSL reading certifi)
- **User-defined transform hooks** — plug your own AST or code-object pass
  into the pack pipeline (obfuscation, docstring stripping, custom bytecode)
- **Zero-install delivery** — ship entire dependency trees over the network to
  machines with nothing but Python installed
- **PyInstaller compatible** — build standalone binaries that load libraries at runtime

## Quick start

```bash
pip install paker
```

### Pack and load in one process

```python
import paker

# 32-byte key. Use os.urandom(32) in real code.
KEY = b"\x42" * 32

# Encrypt `myapp` and its dependencies into a JSON-serializable dict.
bundle = paker.dumps("myapp", key=KEY, include_deps=True)

# Later, possibly elsewhere, hand the key and bundle back to paker.
with paker.loads(bundle, key=KEY):
    import myapp
    myapp.run()
```

The original package doesn't need to be installed on the loading side.
paker's importer takes priority over the filesystem.

### Pack on dev, load on production

```python
# pack.py — run once, on a trusted machine.
import json
import paker

key = open("release.key", "rb").read()        # your secret, your channel
bundle = paker.dumps("myapp", key=key, include_deps=True)
json.dump(bundle, open("app.paker", "w"))
# Ship `app.paker` to production. The key stays behind.
```

```python
# load.py — production. No application source on disk.
import json
import sys
import paker

bundle = json.load(open("app.paker"))
key = sys.stdin.buffer.read(32)               # key arrives out of band
with paker.loads(bundle, key=key):
    import myapp
    myapp.run()
```

### Runtime key delivery (license server pattern)

```python
import os
import paker
import requests

def fetch_key(license_id: str) -> bytes:
    # Your license server returns a 32-byte key tied to this session.
    # paker does not ship this server — you build it.
    resp = requests.post(
        "https://license.example.com/key",
        json={"license": license_id},
    )
    return resp.content

bundle = json.load(open("app.paker"))
key = fetch_key(os.environ["LICENSE_ID"])

with paker.loads(bundle, key=key):
    import myapp
    myapp.run()
# Key is scrubbed from memory when the context manager exits.
```

### Load directly from a dict

```python
import paker

modules = {
    "mylib": {
        "type": "module",
        "extension": "py",
        "code": "def greet(name):\n    return f'Hello, {name}!'\n",
    }
}

with paker.loads(modules) as imp:
    import mylib
    print(mylib.greet("world"))
```

**Portability note:** With the default `compile=False`, pure Python modules
are stored as source and work across any CPython 3.10+ version. With
`compile=True`, bytecode is tied to the CPython minor version it was packed
on. Native extensions (`.so`, `.pyd`) are always tied to the OS and Python
version.

## Transform hooks

paker does not ship obfuscation. When you want one, pass your own callable:

```python
import ast
import paker

def strip_docstrings(tree: ast.AST, module_name: str) -> ast.AST:
    """User-defined AST pass. Runs after ast.parse."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)):
            if (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                node.body.pop(0)
    return tree

bundle = paker.dumps("myapp", key=KEY, ast_transform=strip_docstrings)
```

`code_transform` runs between `compile` and `marshal` for bytecode-level
transforms (requires `compile=True`). See
[`examples/basic/ast_transform_hook.py`](examples/basic/ast_transform_hook.py).

By default, `.py` source is stored as-is. When `ast_transform` or
`strip_annotations` is set, paker reparses and writes `ast.unparse`'d source.
This makes bundles **portable across Python versions** (3.10+). Pass
`compile=True` to store pre-compiled bytecode instead — faster load, but tied
to the CPython minor version that packed it.

## Automatic dependency bundling

Bundle a package with all its transitive dependencies in a single call:

```python
import paker
import json

key = b"\x42" * 32

# Auto-discovers and bundles anthropic + httpx + pydantic + more.
bundle = paker.dumps("anthropic", key=key, include_deps=True)

json.dump(bundle, open("bundle.json", "w"))
```

Dependencies are resolved via `importlib.metadata`. Use `exclude_deps` for
packages you don't want shipped:

```python
bundle = paker.dumps(
    "anthropic", key=key, include_deps=True, exclude_deps={"certifi"},
)
```

## Resource files

Non-Python files (JSON data, PEM certs, templates) that ship with a package
are packed alongside its code. Libraries that use `importlib.resources` or
`pkgutil.get_data` find them transparently:

```python
# Inside a paker-loaded package — no code change needed.
from importlib import resources
data = resources.files("mypkg").joinpath("data/config.json").read_bytes()
```

Libraries that build raw filesystem paths with `os.path.dirname(__file__)` and
pass them to `open()` (e.g. boto3) are handled automatically — paker installs
a lightweight `builtins.open` / `os.path.*` shim by default that routes
`paker://` paths to the bundle. Pass `virtual_fs=False` to `loads()` to
disable. See [docs/architecture.md](docs/architecture.md).

## PyInstaller

paker includes a PyInstaller hook. Build standalone binaries that load libraries
at runtime:

```bash
pip install paker pyinstaller
pyinstaller --onefile my_app.py
```

The resulting binary contains only paker + stdlib. Libraries arrive encrypted
at runtime. See [docs/pyinstaller.md](docs/pyinstaller.md) for details.

## Tested packages

Dump + encrypt + load roundtrip verified with:

| Package | Type | Status |
|---|---|---|
| anthropic | Pure Python + deps | OK |
| boto3 | Pure Python + raw `open()` on data files (needs `virtual_fs`) | OK |
| click | Pure Python | OK |
| fastapi | Pure Python + deps | OK |
| flask | Pure Python + deps | OK |
| jinja2 | Pure Python | OK |
| mcp | Pure Python + deps | OK |
| numpy | Native extensions | OK |
| Pillow | Native extensions + bundled dylibs | OK |
| psutil | Native per-platform | OK |
| pydantic | Pure Python + C core | OK |
| pyyaml | Pure Python + C | OK |
| requests | Pure Python | OK |
| rich | Pure Python | OK |
| pypdfium2 | Native + ctypes | OK |
| sqlalchemy | Pure Python + C | OK |

## API reference

### Dump

```python
paker.dump(
    module, fp, *,
    key=None, compile=False, skip_modules=None, indent=None,
    include_deps=False, exclude_deps=None, include_resources=True,
    strip_metadata=True, strip_annotations=False,
    ast_transform=None, code_transform=None,
    on_import_error=ImportErrorPolicy.AUTO,
)

paker.dumps(
    module, *,
    key=None, compile=False, skip_modules=None,
    include_deps=False, exclude_deps=None,
    include_resources=True, include_native_libs=True,
    strip_metadata=True, strip_annotations=False,
    ast_transform=None, code_transform=None,
    on_import_error=ImportErrorPolicy.AUTO,
) -> dict
```

### Load

```python
paker.load(
    fp, overwrite=False, key=None,
    disk_policy=None, virtual_fs=True,
) -> JsonImporter

paker.loads(
    data, overwrite=False, key=None,
    disk_policy=None, virtual_fs=True,
) -> JsonImporter
```

`data` can be a `dict`, `str`, `bytes`, or `bytearray`.

`disk_policy` controls whether paker writes to disk at runtime:
- `"memory"` (default) — never write to disk
- `"auto"` — allow disk when the platform requires it
- `"disk"` — always use the OS loader via temp files

### Context manager

```python
with paker.loads(data, key=key) as imp:
    import my_module  # available inside this block
# my_module is unloaded here; key bytearray is scrubbed.
```

### Virtual filesystem

Installed automatically by `loads()`. Opt out with `virtual_fs=False`:

```python
paker.loads(data, key=key, virtual_fs=False)
```

Or use the context manager directly for fine-grained control:

```python
with paker.virtual_fs():
    import boto3  # builtins.open + os.path.* route paker:// paths to the bundle
```

## Examples

See the [`examples/`](examples/) directory:

**Basics:**
- [`dump_and_load.py`](examples/basic/dump_and_load.py) — serialize and load modules from JSON
- [`encryption.py`](examples/basic/encryption.py) — AES-256 encrypted modules
- [`ast_transform_hook.py`](examples/basic/ast_transform_hook.py) — plug a user-defined AST pass into the pipeline
- [`bundle_with_deps.py`](examples/basic/bundle_with_deps.py) — auto-discover and bundle dependencies
- [`cli_usage.py`](examples/basic/cli_usage.py) — command-line interface

**Real packages:**
- [`psutil_system_info.py`](examples/packages/psutil_system_info.py) — system monitoring from an encrypted bundle
- [`requests_http.py`](examples/packages/requests_http.py) — HTTP requests from a bundle
- [`numpy_compute.py`](examples/packages/numpy_compute.py) — numpy with native extensions from a bundle
- [`pydantic_models.py`](examples/packages/pydantic_models.py) — data validation with auto-bundled deps

**Advanced:**
- [`ip_protection/`](examples/ip_protection/) — end-to-end IP protection.
  A vendor packs a proprietary algorithm, a mock license server hands out
  per-session keys, a customer runs it without the source ever reaching
  disk. Includes an `attack.py` that walks through the static attack
  surfaces a bundle-only adversary would try.
- [`remote_agent/`](examples/remote_agent/) — drive an AI agent on a
  zero-install client: the server holds the Anthropic API key, packs
  libraries on demand, and ships `{libraries, code}` over TCP on every
  tool call. The client imports bundles as they arrive and executes code
  against a persistent globals dict.

## Security

See [docs/security.md](docs/security.md) for a detailed analysis of paker's
security model — what it protects, what it doesn't, what an attacker sees at
each layer, and recommendations for maximum protection.

## License

MIT. The vendored `MemoryModule.c` is MPL-2.0. The vendored `tiny-AES-c` is
public domain.
