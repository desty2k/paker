# Paker — Development Guidelines

## Core Design Principles

**Stealth loader first.** paker is a packager + importer. It packs Python
packages into a JSON bundle and imports them back from memory with optional
encryption. Module-level obfuscation is explicitly OUT of scope — we ship
nothing beyond `strip_metadata` and `strip_annotations` flags. Users who want
obfuscation plug into the `ast_transform` / `code_transform` hooks on
`paker.dumps()`. See `examples/basic/ast_transform_hook.py`.

**Zero disk footprint.** The loader avoids writing to disk whenever the
platform allows it. Linux: `memfd_create`. macOS: write → `dlopen` → unlink
(file exists for milliseconds). Windows: in-memory PE loading via
`_memimporter`. The tempfile fallback is a last resort, not a default.

**Stealth.** Loaded modules leave minimal observable traces. No leftover temp
files, no background cleanup processes, no bat files killing PIDs. Any file
created temporarily is removed as soon as the module is loaded into memory.

**Cross-platform.** Every feature works on Linux, macOS, and Windows.
Platform-specific code lives in `paker/_loaders/` with a strategy pattern —
the loader factory auto-selects the best available strategy.

## Public API

```python
paker.dumps(
    module,
    *,
    key=None,                      # bytes — AES-256-CTR + HMAC per module
    include_deps=False,
    exclude_deps=None,
    skip_modules=None,
    on_import_error=ImportErrorPolicy.AUTO,
    strip_metadata=True,           # blank co_filename + co_qualname (stealth)
    strip_annotations=False,       # strip type hints + asserts (breaks pydantic/FastAPI)
    ast_transform=None,            # Callable[[ast.AST, str], ast.AST]
    code_transform=None,           # Callable[[types.CodeType, str], types.CodeType]
    compile=False,                 # True: marshal bytecode (fast, version-tied); False: store source (portable)
    include_resources=True,        # pack non-Python data files (JSON, PEM, templates)
    include_native_libs=True,      # pack bundled .so/.dylib/.dll deps
) -> dict
```

`paker.dump` mirrors `dumps` and writes to a file (adds `fp` and `indent`
parameters).

```python
paker.loads(
    s,                             # dict | str | bytes | bytearray
    *,
    overwrite=False,
    key=None,                      # 32-byte decryption key
    disk_policy=None,              # DiskPolicy: "memory" (default) | "auto" | "disk"
    as_file_strategy=None,         # override: AsFileStrategy for as_file only
    virtual_fs=True,               # install builtins.open / os.path shim for paker:// paths
    native_loader=None,            # override: NativeLoaderPolicy for native exts only
) -> JsonImporter
```

`paker.load` mirrors `loads` and reads from a file handle.

### Disk policy

Single knob controlling whether paker writes to disk at runtime:

```python
paker.loads(bundle, key=KEY)                       # memory (default)
paker.loads(bundle, key=KEY, disk_policy="auto")   # allow disk when platform requires
paker.loads(bundle, key=KEY, disk_policy="disk")   # always use OS loader via temp files
```

| Policy | `as_file` behavior | Native extension loading |
|--------|-------------------|------------------------|
| `memory` | memfd / `/dev/fd/N`; raises on Windows | In-memory (memfd, MemoryModule, dlopen+unlink) |
| `auto` | memfd / `/dev/fd/N`; tempfile fallback on Windows | In-memory where possible |
| `disk` | Always tempfile | Always tempfile + OS loader (LoadLibrary / dlopen) |

`as_file_strategy` and `native_loader` override the corresponding
subsystem when set alongside `disk_policy`. All kwargs are programmatic
only — the environment is not consulted.

Enterprise deployments with endpoint security products should use
``disk_policy="disk"`` and work with their security team on application
allowlisting.

### Resources (certifi, boto3, jinja2.PackageLoader)

When `include_resources=True` (default), paker packs non-Python files
sitting alongside module code into a `resources: {posix_relpath: blob}`
dict per package node. Three runtime code paths serve them:

1. **`importlib.resources`** (`files(pkg).joinpath(x).read_bytes()`) — always
   works, implemented via `JsonImporter.get_resource_reader()` returning a
   `TraversableResources`. Transparent, no opt-in needed.
2. **`pkgutil.get_data(pkg, relpath)`** — same, via `JsonImporter.get_data()`.
3. **Raw `open()` / `os.path.isfile` / `os.listdir`** on `__file__`-relative
   paths. Handled by paker's virtual filesystem shim, which patches
   `builtins.open`, `os.path.{isfile,isdir,exists,abspath}`, `os.listdir`,
   `os.scandir`, and `os.stat` to recognise `paker://` prefixes. Installed
   automatically by `paker.loads()` (pass `virtual_fs=False` to disable).
   Also available as a standalone `paker.virtual_fs()` context manager.

The shim also intercepts `ctypes.CDLL.__init__` for `paker://` paths —
the native lib is materialized via memfd (Linux), tempfile+codesign
(macOS), or tempfile (Windows) and the real path is passed to the
original `CDLL`.

**`virtual_fs` limitation**: `open(paker://...)` returns `io.BytesIO` /
`io.StringIO`. Code that needs a real OS file descriptor (`.fileno()`,
OpenSSL C-layer callbacks) must go through `importlib.resources.as_file`
instead, which is controlled by `disk_policy`.

## Architecture

- `paker/__init__.py` — public API.
- `paker/importers/jsonimporter.py` — PEP 451 `MetaPathFinder` + `Loader`.
- `paker/_loaders/` — platform-specific native-extension loaders.
  - `_linux.py` — `MemfdLoader` (memfd_create + dlopen). Pre-loads bundled
    `.so` deps with `RTLD_GLOBAL` in topologically-sorted order so the
    main extension's `DT_NEEDED` lookups resolve by soname.
  - `_macos.py` — `UnlinkLoader` (write + dlopen + unlink). For packages
    with bundled `.dylibs/`, recreates the sibling tree under `mkdtemp`,
    dlopens, and `shutil.rmtree`s the whole tree; mmap keeps the bytes
    valid for the lifetime of the process.
  - `_windows.py` — `MemImporterLoader` (`_memimporter` C extension).
    Serves bundled DLLs via the `_memimporter` dependency callback.
    Extensions with Static TLS (Rust, some C++) fall back to tempfile +
    LoadLibrary in `%LOCALAPPDATA%/pyextcache`.
  - `_tempfile.py` — `TempFileLoader` (universal fallback).
  - `_resource_paths.py` — memory-backed path factory for
    `importlib.resources.as_file` (memfd / `/dev/fd/N` / Windows tempfile).
- `paker/_native/` — discovery of bundled native libs (delocate `.dylibs/`,
  auditwheel `<pkg>.libs/`, delvewheel `*.libs/`) + a minimal pure-Python
  ELF parser for `DT_NEEDED` / `DT_SONAME`.
- `paker/importers/_virtual_fs.py` — virtual filesystem shim
  (`builtins.open`, `os.path.*`, `os.listdir`, `os.scandir`, `os.stat`,
  `ctypes.CDLL.__init__`). Auto-installed by `paker.loads()`.
- `paker/importers/_resources.py` — `importlib.resources.as_file`
  singledispatch handler for `_BundleTraversable`.
- `paker/_crypto.py` — AES-256-CTR + HMAC-SHA256 encryption helpers.
- `paker/deps.py` — transitive dependency discovery via `importlib.metadata`.
- `csrc/crypto/` — C extension for secure decryption + memory scrubbing.
- `csrc/memimporter/` — Windows-only in-memory PE loader (from py2exe +
  paker patches: `RtlAddFunctionTable` for x64 SEH/C++ unwind).

## Python Version Support

Python >= 3.10. No backward compatibility with older versions.

## Code Comments

Comments and docstrings describe the **current state** of the code, not the
journey, decision rationale, or threat model. No "because EDR…", no "we chose
this over X because…", no "belt-and-suspenders". If a comment wouldn't help
someone reading the code cold, remove it. Design rationale belongs in commit
messages, PR descriptions, and `paker-story/` files.

## Testing

Run unit tests with `python -m pytest tests/ -v`. Platform-specific tests
are marked with `skipif`. Real-package smoke tests (bundles not shipped in
the repo) cover click, psutil, jinja2, pydantic, requests, pyyaml, rich,
sqlalchemy, numpy, boto3, anthropic, flask, fastapi, Pillow, mcp,
pypdfium2 — all expected to pass at plaintext and encrypted configs.
Tests must pass on the current platform before committing.

## Obfuscation

Paker does not ship an obfuscation pipeline. Use the `ast_transform` /
`code_transform` hooks on `paker.dumps()`:

```python
paker.dumps("mypackage", key=KEY, ast_transform=my_pass)
```

See `examples/basic/ast_transform_hook.py` for a runnable example.
