# Architecture

How paker maps Python's import system onto encrypted JSON, and how it loads
code and data from that bundle without writing to disk.

## Import system

paker registers a PEP 451 `MetaPathFinder` at the front of `sys.meta_path`.
When Python imports a module, paker checks its JSON dict first — before the
filesystem. Pure Python modules are compiled and exec'd. Bytecode modules are
unmarshalled. Native extensions go through platform-specific loaders.

Submodules are discovered via `importlib.util.find_spec` and compiled from
source without being executed — packing a library never runs its module-level
code, so diagnostic modules (`PIL.report`), platform-specific imports, and
optional-dep guards don't fire in the packer's process.

## Native extension loading

| Platform | Strategy | Disk writes |
|---|---|---|
| Linux | `memfd_create` + `dlopen("/proc/self/fd/N")` | None |
| macOS | Write to tmpdir → `dlopen` → `unlink` immediately | Milliseconds |
| Windows | `MemoryModule.c` — in-memory PE section mapping | None |
| Windows (Static TLS) | Tempfile + `LoadLibrary` in `%LOCALAPPDATA%/pyextcache` | Process lifetime |
| Fallback | Temp file + `importlib` | Cleaned at exit |

`disk_policy="disk"` on `loads()` forces all extensions through the tempfile +
OS loader path regardless of platform.

Linux native extensions never touch the filesystem — they live in named
memory-backed file descriptors. The fds stay open for the process lifetime
because glibc's dlopen caches by `/proc/self/fd/N` path string — if an fd
number were reused, a later dlopen would return the wrong handle. On macOS, the file exists for milliseconds
between write and `unlink`; the kernel keeps the `mmap`'d pages valid after
deletion (same semantics as Apple's own `dlopen`, which writes to `$TMPDIR`
internally since dyld3). On Windows, the PE loader maps DLL sections directly
into `VirtualAlloc`'d memory.

## Bundled native dependencies

Packages that ship bundled native dependencies (Pillow's `PIL/.dylibs/`,
numpy's `numpy.libs/`, delvewheel's `*.libs/`) are packed alongside the main
extension.

On Linux, a pure-Python ELF parser extracts `DT_NEEDED` / `DT_SONAME` at pack
time; the loader preloads deps in topological order with
`dlopen(RTLD_NOW | RTLD_GLOBAL)` so the main extension's soname lookups
resolve without the dynamic linker walking `$ORIGIN`.

On macOS, the sibling `.dylibs/` tree is recreated under a tempdir,
`dlopen`'d, then torn down with `shutil.rmtree`. POSIX mmap keeps the pages
valid for the lifetime of the process.

On Windows, bundled DLLs go through `_memimporter`'s dependency callback so
the PE import table resolves from in-memory blobs rather than `LoadLibraryA`
on disk.

## Resources and data files

Non-Python files (JSON data, PEM certs, Jinja templates) living alongside
module code are packed under a per-package `resources` dict. paker implements
`importlib.resources.TraversableResources` so `resources.files(pkg)` and
`pkgutil.get_data` route through the bundle transparently — no code changes
in the consuming library.

For C-layer callers that need a real filesystem path
(`ssl.create_default_context(cafile=...)` → OpenSSL → `fopen`), paker
registers an `importlib.resources.as_file` handler that serves a memory-backed
fd path: `memfd_create` on Linux, `mkstemp + unlink → /dev/fd/N` on macOS.
Windows has no user-mode equivalent primitive; the default
(`disk_policy="memory"`) raises rather than spill to disk. `disk_policy="auto"`
falls back to a short-lived tempfile in `%TEMP%`.

Some libraries (boto3's `FileLoader`) build raw filesystem paths from
`os.path.dirname(__file__)` and pass them to `builtins.open` / `os.path.isfile`
rather than going through `importlib.resources`. Those go through
paker's virtual filesystem shim, which patches `builtins.open`,
`os.path.{isfile,isdir,exists,abspath}`, `os.listdir`, `os.scandir`, and
`os.stat` to recognise `paker://` prefixes. The shim is installed
automatically by `paker.loads()` (pass `virtual_fs=False` to disable).
`paker.virtual_fs()` is also available as a standalone context manager for
fine-grained control.

The shim also intercepts `ctypes.CDLL.__init__` for `paker://` paths —
the native lib is materialized via memfd (Linux), tempfile+codesign
(macOS), or tempfile (Windows) and the real path is passed to the
original `CDLL`.

## Encryption pipeline

Two storage modes controlled by the `compile` parameter on `dumps()`:

**`compile=False` (default)** -- portable source storage:
```
dump:  source -> [optional AST transform/unparse] -> AES-256-CTR encrypt -> HMAC-SHA256 -> base64 -> JSON
load:  JSON -> base64 -> HMAC verify -> AES-256-CTR decrypt (in C) -> Py_CompileString (in C) -> memset -> exec
```

When no AST-changing option is enabled, source text is stored unchanged. If
`ast_transform` or `strip_annotations` is enabled, paker serializes the
resulting AST with `ast.unparse`.

**`compile=True`** -- pre-compiled bytecode storage:
```
dump:  source -> [user ast_transform] -> compile(optimize=2) -> [user code_transform] -> marshal -> AES-256-CTR encrypt -> HMAC-SHA256 -> base64 -> JSON
load:  JSON -> base64 -> HMAC verify -> AES-256-CTR decrypt (in C) -> unmarshal -> exec
```

The default (`compile=False`) produces bundles portable across CPython 3.10+
versions. `compile=True` ties the bundle to the CPython minor version that
packed it but allows `code_transform` hooks and faster load times.

Separate encryption and MAC keys are derived from the master key via HKDF.
The C extension (`_paker_crypto`) handles decryption in `malloc`'d buffers
zeroed via a volatile-pointer loop that the compiler can't optimize away. The
runtime key lives in a `bytearray` that's scrubbed on importer unload.
