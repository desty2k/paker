"""Paker — pack Python packages into a JSON bundle, load them from memory.

Packs ``.py`` modules as portable source (default) or pre-compiled bytecode
(``compile=True``), optionally encrypted with AES-256-CTR + HMAC-SHA256.
The loader (:class:`paker.importers.JsonImporter`) is a PEP 451
``MetaPathFinder`` / ``Loader`` that decrypts and imports modules in memory —
with platform-specific paths that avoid touching the disk for native
extensions (Linux: ``memfd_create``, Windows: in-memory PE loading,
macOS: write → dlopen → unlink).

``ast_transform`` and ``code_transform`` hooks on :func:`dumps` let users
plug their own AST or bytecode passes into the pack pipeline.

See ``examples/`` for hook samples.
"""

from __future__ import annotations

import ast
import base64
import enum
import importlib
import importlib.machinery
import importlib.util
import io
import json
import logging
import marshal
import os
import pkgutil
import re
import types
from collections.abc import Callable
from importlib.machinery import SourceFileLoader

from paker._loaders._base import NativeLoaderPolicy
from paker._loaders._resource_paths import AsFileStrategy
from paker._policy import DiskPolicy
from paker.exceptions import PakerDumpError, PakerImportError
from paker.importers import JsonImporter
from paker.importers._virtual_fs import virtual_fs
from paker.utils import check_compatibility


__all__ = [
    "dump", "dumps", "load", "loads", "mp_initializer",
    "ImportErrorPolicy", "AsFileStrategy", "NativeLoaderPolicy",
    "DiskPolicy", "virtual_fs", "__version__",
]
__version__ = "2.0.0b1"


class ImportErrorPolicy(enum.Enum):
    AUTO = "auto"
    SKIP = "skip"
    RAISE = "raise"


AstTransform = Callable[[ast.AST, str], ast.AST]
"""Hook signature for ``ast_transform``. Receives the parsed AST and the
module's full dotted name; returns a (possibly-modified) AST."""

CodeTransform = Callable[[types.CodeType, str], types.CodeType]
"""Hook signature for ``code_transform``. Receives the compiled code object
and the module's full dotted name; returns a (possibly-modified) code object.
Used for bytecode-level transforms like custom metadata stripping."""


_PLATFORM_RE = re.compile(
    r"_?(aix|linux|osx|macos|windows|win32|win(?:dows|console|term|api|types)|sunos|solaris|freebsd|netbsd|openbsd|bsd|darwin|cygwin|posix|android)\b",
    re.IGNORECASE,
)
_PLATFORM_PREFIX_RE = re.compile(
    r"^_?(win|linux|osx|macos|darwin|bsd|aix|sunos|solaris|freebsd|cygwin|posix|android)",
    re.IGNORECASE,
)
_IMPORT_NAME_RE = re.compile(r"cannot import name '([^']+)'")
_OPTIONAL_MSG_RE = re.compile(
    r"can only be imported on"
    r"|(?:is|must be|needs to be) (?:required|installed)(?: to (?:use|import))?"
    r"|install.+with.+pip install"
    r"|requires.+extra",
    re.IGNORECASE,
)

_logger = logging.getLogger("paker")


# ---------------------------------------------------------------------------
# Platform / dependency error classification
# ---------------------------------------------------------------------------


def _should_skip_import_error(exc: ImportError, target: str) -> bool:
    """Return True when an ImportError during pack should be silently skipped.

    Triggers:
    1. Platform-specific modules (wrong OS native extensions).
    2. Error messages explicitly naming a platform restriction or optional dep.
    3. Missing optional dependencies not installed on ``sys.path`` anywhere.
    """
    msg = str(exc)
    candidates = {target}

    exc_name = getattr(exc, "name", None)
    if exc_name:
        candidates.add(exc_name)

    msg_match = _IMPORT_NAME_RE.search(msg)
    if msg_match:
        candidates.add(msg_match.group(1))

    if _OPTIONAL_MSG_RE.search(msg):
        _logger.debug("skipping %s (optional dependency: %s)", target, msg[:80])
        return True

    for name in candidates:
        try:
            spec = importlib.util.find_spec(name)
        except (ValueError, ImportError, ModuleNotFoundError):
            spec = None
        if spec and spec.origin:
            is_native = any(
                spec.origin.endswith(ext)
                for ext in importlib.machinery.EXTENSION_SUFFIXES
            )
            if is_native:
                _logger.debug("skipping %s (native extension: %s)", target, name)
                return True

    for name in candidates:
        if _PLATFORM_RE.search(name):
            _logger.debug("skipping %s (platform identifier: %s)", target, name)
            return True

    failed = exc_name or target
    try:
        spec = importlib.util.find_spec(failed)
    except (ValueError, ImportError, ModuleNotFoundError):
        spec = None
    if spec is None:
        _logger.debug("skipping %s (dependency not installed: %s)", target, failed)
        return True

    # ``from X import Y`` where ``Y`` is an optional dep: Python reports
    # ``name=X`` (installed) but ``Y`` captured from the message isn't on
    # sys.path. Only applies when the user isn't directly packaging X.Y.
    if exc_name and exc_name != target:
        for name in candidates - {exc_name}:
            try:
                spec = importlib.util.find_spec(name)
            except (ValueError, ImportError, ModuleNotFoundError):
                spec = None
            if spec is None:
                _logger.debug(
                    "skipping %s (optional dependency not installed: %s)",
                    target, name,
                )
                return True

    return False


def _get_active_importer() -> JsonImporter | None:
    import sys
    for finder in sys.meta_path:
        if isinstance(finder, JsonImporter):
            return finder
    return None


# ---------------------------------------------------------------------------
# Public API: load / loads
# ---------------------------------------------------------------------------


def load(
    fp: io.IOBase,
    overwrite: bool = False,
    key: bytes | None = None,
    disk_policy: DiskPolicy | str | None = None,
    as_file_strategy: AsFileStrategy | str | None = None,
    virtual_fs: bool = True,
    native_loader: NativeLoaderPolicy | str | None = None,
) -> JsonImporter:
    """Deserialize a paker bundle from ``fp`` and return an active importer."""
    return loads(
        fp.read(),
        overwrite=overwrite,
        key=key,
        disk_policy=disk_policy,
        as_file_strategy=as_file_strategy,
        virtual_fs=virtual_fs,
        native_loader=native_loader,
    )


def loads(
    s: str | dict | bytes | bytearray,
    overwrite: bool = False,
    key: bytes | None = None,
    disk_policy: DiskPolicy | str | None = None,
    as_file_strategy: AsFileStrategy | str | None = None,
    virtual_fs: bool = True,
    native_loader: NativeLoaderPolicy | str | None = None,
) -> JsonImporter:
    """Deserialize a paker bundle (dict, str, or bytes) and return an active importer.

    ``disk_policy`` is the primary knob controlling whether paker writes to disk:

    * ``MEMORY`` (default) — never write to disk. ``as_file`` raises on Windows.
    * ``AUTO`` — allow disk when the platform requires it.
    * ``DISK`` — always use the OS loader via temp files.

    ``as_file_strategy`` and ``native_loader`` override the corresponding
    subsystem when set alongside ``disk_policy``.

    All kwargs are programmatic only — the environment is not consulted.
    """
    if not isinstance(s, (dict, str, bytes, bytearray)):
        raise TypeError(
            f"the module dict object must be dict, str, bytes or bytearray, "
            f"not {s.__class__.__name__}"
        )
    if isinstance(s, (bytes, bytearray)):
        s = s.decode(json.detect_encoding(s), "surrogatepass")
    if isinstance(s, str):
        s = json.loads(s)
    check_compatibility(s)

    from paker._policy import resolve_disk_policy, map_disk_policy
    resolved_policy = resolve_disk_policy(disk_policy)
    if resolved_policy is not None:
        default_as_file, default_native = map_disk_policy(resolved_policy)
        if as_file_strategy is None:
            as_file_strategy = default_as_file
        if native_loader is None:
            native_loader = default_native

    from paker._loaders._base import get_native_loader, _resolve_loader_policy
    resolved_loader = get_native_loader(_resolve_loader_policy(native_loader))

    importer = _get_active_importer()
    if importer is not None:
        if key is not None and importer._key != key:
            importer = JsonImporter(
                s, key=key, as_file_strategy=as_file_strategy,
                virtual_fs=virtual_fs, native_loader=resolved_loader,
            )
        else:
            for module_name, module_data in s.items():
                if importer.find_spec(module_name, None) is not None:
                    if overwrite:
                        importer.add_module(module_name, module_data)
                else:
                    importer.add_module(module_name, module_data)
    else:
        importer = JsonImporter(
            s, key=key, as_file_strategy=as_file_strategy,
            virtual_fs=virtual_fs, native_loader=resolved_loader,
        )
    return importer


def mp_initializer(
    bundle: dict,
    key: bytes | None = None,
    **kwargs,
) -> tuple[Callable, tuple]:
    """Return ``(func, args)`` for bootstrapping paker in a child process.

    Pass the result to ``multiprocessing.Pool(initializer=..., initargs=...)``
    or ``concurrent.futures.ProcessPoolExecutor(initializer=..., initargs=...)``.
    The child calls ``paker.loads(bundle, key=key)`` on startup so bundled
    modules are importable in worker processes.

    The ``spawn`` start method (macOS/Windows default) starts a fresh
    interpreter — without this initializer, paker-loaded modules are
    invisible to child processes.

    ::

        bundle = paker.dumps("myapp", key=KEY, include_deps=True)
        init_fn, init_args = paker.mp_initializer(bundle, key=KEY)

        with ProcessPoolExecutor(initializer=init_fn, initargs=init_args) as pool:
            pool.submit(myapp.heavy_task, data)

    .. warning::

       The key is serialized through the standard multiprocessing pickle
       channel (a local pipe). Do not use with ``multiprocessing.managers``
       or any networked IPC — that would send the key in plaintext over
       a network socket.
    """
    return _mp_child_init, (bundle, key, kwargs)


def _mp_child_init(bundle: dict, key: bytes | None, kwargs: dict) -> None:
    """Target for ``mp_initializer``. Module-level so it's picklable."""
    loads(bundle, key=key, **kwargs)


# ---------------------------------------------------------------------------
# Public API: dump / dumps
# ---------------------------------------------------------------------------


def dump(
    module: str | types.ModuleType,
    fp: io.IOBase,
    *,
    skip_modules: list[str | types.ModuleType] | None = None,
    indent: int | None = None,
    key: bytes | None = None,
    compile: bool = False,
    on_import_error: ImportErrorPolicy = ImportErrorPolicy.AUTO,
    include_deps: bool = False,
    exclude_deps: set[str] | None = None,
    include_resources: bool = True,
    strip_metadata: bool = True,
    strip_annotations: bool = False,
    ast_transform: AstTransform | None = None,
    code_transform: CodeTransform | None = None,
) -> None:
    """Serialize a Python module (and optionally its dependencies) as JSON to ``fp``.

    Thin wrapper around :func:`dumps` — see that function for parameter details.
    """
    fp.write(json.dumps(
        dumps(
            module,
            skip_modules=skip_modules,
            key=key,
            compile=compile,
            on_import_error=on_import_error,
            include_deps=include_deps,
            exclude_deps=exclude_deps,
            include_resources=include_resources,
            strip_metadata=strip_metadata,
            strip_annotations=strip_annotations,
            ast_transform=ast_transform,
            code_transform=code_transform,
        ),
        indent=indent,
    ))


def dumps(
    module: str | types.ModuleType,
    *,
    skip_modules: list[str | types.ModuleType] | None = None,
    key: bytes | None = None,
    compile: bool = False,
    on_import_error: ImportErrorPolicy = ImportErrorPolicy.AUTO,
    include_deps: bool = False,
    exclude_deps: set[str] | None = None,
    include_resources: bool = True,
    include_native_libs: bool = True,
    strip_metadata: bool = True,
    strip_annotations: bool = False,
    ast_transform: AstTransform | None = None,
    code_transform: CodeTransform | None = None,
) -> dict:
    """Serialize a Python module (and optionally its dependencies) as a dict.

    Every ``.py`` source is parsed, optionally transformed through
    ``ast_transform``, compiled with ``optimize=2``, optionally transformed
    through ``code_transform``, and marshalled. When ``key`` is supplied each
    module's marshalled bytes are AES-256-CTR encrypted and HMAC-SHA256
    signed with a key derived from ``key`` and the module's full name.

    Parameters:
        module: name (``"pkg.sub"``) or imported module object to pack.
        skip_modules: submodule names to exclude from the walk.
        key: 32-byte encryption key. When ``None`` the bundle ships plaintext.
        on_import_error: policy for handling submodules that fail to import
            at pack time. ``AUTO`` skips platform-specific and missing-dep
            imports automatically; ``SKIP`` logs and skips all; ``RAISE``
            propagates the error.
        include_deps: also pack all transitive dependencies discovered via
            ``importlib.metadata``.
        exclude_deps: names of transitive deps to omit (for optional packages
            or ones that hit paker limitations).
        include_resources: also pack non-Python files (JSON data, PEM certs,
            templates, any file that isn't ``.py`` / ``.pyc`` / ``.so`` /
            ``.pyd`` / ``.dll``). Served to library code at import time via
            ``importlib.resources.files(pkg)`` / ``pkgutil.get_data``. Required
            for certifi, boto3, jinja2.PackageLoader, and similar packages.
        strip_metadata: scrub ``co_filename`` / ``co_qualname`` from each code
            object so decompiled output doesn't leak the packager's local
            paths. Zero runtime cost. Default on.
        strip_annotations: remove ``ast.AnnAssign`` annotations and assert
            statements before compile. **Breaks pydantic, FastAPI, dataclasses
            and anything else that introspects ``__annotations__`` at runtime**
            — opt in only when you know your target doesn't need them.
        ast_transform: optional callback ``(tree, module_name) -> tree`` run
            between ``ast.parse`` and ``compile``. This is where user-defined
            obfuscation, LLM-poisoning docstring injection, dead-code insertion
            etc. plug in. Paker ships no obfuscation; see ``examples/``.
        code_transform: optional callback ``(code_obj, module_name) -> code_obj``
            run between ``compile`` and ``marshal``. Used for bytecode-level
            transforms; the built-in ``strip_metadata`` path uses the same
            machinery internally.
    """
    module_name = module if isinstance(module, str) else module.__name__

    if include_deps:
        from paker.deps import find_dependencies
        packages = find_dependencies(module_name, exclude=exclude_deps)
    else:
        packages = [module_name]

    options = _DumpOptions(
        key=key,
        compile=compile,
        strip_metadata=strip_metadata,
        strip_annotations=strip_annotations,
        include_resources=include_resources,
        include_native_libs=include_native_libs,
        ast_transform=ast_transform,
        code_transform=code_transform,
    )

    result = {}
    for pkg_name in packages:
        try:
            mod = importlib.import_module(pkg_name) if isinstance(pkg_name, str) else pkg_name
        except ImportError:
            _logger.debug("skipping dependency %s (not importable)", pkg_name)
            continue

        if not isinstance(getattr(mod, "__loader__", None), SourceFileLoader):
            if not hasattr(mod, "__path__"):
                _logger.debug("skipping %s (not a source module)", pkg_name)
                continue
            module_file = getattr(mod, "__file__", None)
            if module_file is None:
                pass
            elif not isinstance(mod.__loader__, SourceFileLoader):
                _logger.debug(
                    "skipping %s (loader: %s)",
                    pkg_name, type(mod.__loader__).__name__,
                )
                continue

        skip = list(skip_modules) if skip_modules else ["__main__"]
        dict_key = mod.__package__ or mod.__name__
        try:
            node = _dump(mod, skip, options, on_import_error)
        except PakerDumpError as e:
            if on_import_error is ImportErrorPolicy.RAISE:
                raise
            _logger.warning("skipping %s: %s", pkg_name, e)
            continue
        # Capture distribution metadata so importlib.metadata.version()
        # works at load time for packages that check their own version.
        dist_version = _get_dist_version(pkg_name)
        if dist_version:
            node["dist_name"] = dist_version[0]
            node["dist_version"] = dist_version[1]
        result[dict_key] = node

    return result


def _get_dist_version(import_name: str) -> tuple[str, str] | None:
    """Return ``(dist_name, version)`` for a top-level import name, or None."""
    try:
        pkg_map = importlib.metadata.packages_distributions()
    except Exception:
        return None
    dists = pkg_map.get(import_name)
    if not dists:
        return None
    try:
        d = importlib.metadata.distribution(dists[0])
        return (d.name, d.version)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Internal packer
# ---------------------------------------------------------------------------


class _DumpOptions:
    """Bundle of per-dump pack-time options shared across the recursive walk.

    Keeping this internal — callers use the :func:`dumps` kwargs. Consolidating
    the plumbing here avoids a parameter-soup down through ``_dump`` /
    ``_pack_source`` as we add new per-dump state in the future.
    """

    __slots__ = (
        "key", "compile", "strip_metadata", "strip_annotations",
        "include_resources", "include_native_libs",
        "ast_transform", "code_transform",
    )

    def __init__(
        self,
        *,
        key: bytes | None,
        compile: bool,
        strip_metadata: bool,
        strip_annotations: bool,
        include_resources: bool,
        include_native_libs: bool,
        ast_transform: AstTransform | None,
        code_transform: CodeTransform | None,
    ):
        self.key = key
        self.compile = compile
        self.strip_metadata = strip_metadata
        self.strip_annotations = strip_annotations
        self.include_resources = include_resources
        self.include_native_libs = include_native_libs
        self.ast_transform = ast_transform
        self.code_transform = code_transform


def _iter_submodules(module: types.ModuleType):
    """Yield ``(name, is_package)`` for all immediate submodules of a package.

    Combines ``pkgutil.iter_modules`` (regular modules and packages) with a
    filesystem scan for namespace packages (directories without ``__init__.py``
    that still contain ``.py`` files).
    """
    seen = set()
    for finder, name, is_pkg in pkgutil.iter_modules(module.__path__):
        seen.add(name)
        yield name, is_pkg

    for path_entry in module.__path__:
        if not os.path.isdir(path_entry):
            continue
        for item in os.listdir(path_entry):
            if item in seen or item.startswith(".") or item == "__pycache__":
                continue
            item_path = os.path.join(path_entry, item)
            if os.path.isdir(item_path) and not os.path.exists(
                os.path.join(item_path, "__init__.py"),
            ):
                has_py = any(f.endswith(".py") for f in os.listdir(item_path))
                if has_py:
                    seen.add(item)
                    yield item, True


def _dump(
    module: types.ModuleType,
    skip_modules: list,
    options: _DumpOptions,
    on_import_error: ImportErrorPolicy,
) -> dict:
    """Build the bundle dict for ``module`` (recursive for packages)."""
    is_package = hasattr(module, "__path__")

    if is_package:
        module_file = getattr(module, "__file__", None)
        if module_file is not None:
            extension, code = _pack_file(
                module_file, module.__name__, options,
            )
            encrypted = options.key is not None
        else:
            # Namespace package — directory without ``__init__.py``. There's no
            # code to pack; store an empty marker node and skip encryption
            # (no HMAC tag to verify against).
            extension, code = "py", ""
            encrypted = False

        result = {
            "type": "package",
            "extension": extension,
            "code": code,
        }
        if encrypted:
            result["encrypted"] = True

        if options.include_resources:
            resources = _pack_resources(module, options)
            if resources:
                result["resources"] = resources

        if options.include_native_libs:
            native_libs = _pack_native_libs(module, options)
            if native_libs:
                result["native_libs"] = native_libs

        result["modules"] = {}
        for module_name, _is_sub_pkg in _iter_submodules(module):
            if module_name in skip_modules:
                continue

            full_name = module.__name__ + "." + module_name
            try:
                node = _pack_submodule(
                    full_name, module_name, skip_modules, options, on_import_error,
                )
            except PakerDumpError as e:
                if on_import_error is ImportErrorPolicy.RAISE:
                    raise
                _logger.warning("skipping %s: %s", full_name, e)
                continue
            if node is not None:
                result["modules"][module_name] = node
    else:
        module_file = getattr(module, "__file__", None)
        if module_file is None:
            raise PakerDumpError(f"module {module.__name__} has no __file__")
        extension, code = _pack_file(
            module_file, module.__name__, options,
        )
        result = {
            "type": "module",
            "extension": extension,
            "code": code,
        }
        if options.key is not None:
            result["encrypted"] = True
    return result


def _pack_submodule(
    full_name: str,
    short_name: str,
    skip_modules: list,
    options: _DumpOptions,
    on_import_error: ImportErrorPolicy,
) -> dict | None:
    """Pack one submodule by spec — no ``import_module``, no side effects.

    ``importlib.util.find_spec`` locates the source file without executing
    the module body. The file is read + compiled + marshalled exactly the
    way a runtime ``exec`` would have done — but only at load time in the
    consumer process, so pack time stays free of the target library's
    module-level prints, network calls, or process-wide state changes.

    Returns ``None`` when the submodule should be skipped (platform-specific,
    optional dep, unreachable namespace package).
    """
    try:
        spec = importlib.util.find_spec(full_name)
    except (ImportError, ModuleNotFoundError, ValueError) as e:
        if on_import_error is ImportErrorPolicy.SKIP:
            _logger.warning("skipping %s: %s", full_name, e)
            return None
        # find_spec can raise for sub-modules whose parent triggers a failed
        # import; treat like a real import error under AUTO.
        if on_import_error is ImportErrorPolicy.AUTO and (
            isinstance(e, ImportError)
            and _should_skip_import_error(e, full_name)
        ):
            return None
        raise PakerDumpError(f"find_spec {full_name}: {e}") from e

    if spec is None:
        if on_import_error is ImportErrorPolicy.RAISE:
            raise PakerDumpError(f"no spec for {full_name}")
        _logger.debug("skipping %s (no spec)", full_name)
        return None

    # Package — recurse. We need a lightweight stand-in with __name__,
    # __path__, __file__ for _dump's package branch. Import only here,
    # after find_spec confirmed it exists, because sub-package recursion
    # still requires ``__path__`` to iterate children — and namespace
    # packages only expose a real ``_NamespacePath`` via import.
    if spec.submodule_search_locations is not None:
        try:
            sub_module = importlib.import_module(full_name)
        except ImportError as e:
            if on_import_error is ImportErrorPolicy.SKIP:
                _logger.warning("skipping %s: %s", full_name, e)
                return None
            if on_import_error is ImportErrorPolicy.AUTO and _should_skip_import_error(e, full_name):
                return None
            raise PakerDumpError(f"import {full_name}: {e}") from e
        except Exception as e:
            if on_import_error is ImportErrorPolicy.SKIP:
                _logger.warning("skipping %s: %s: %s", full_name, type(e).__name__, e)
                return None
            last_part = full_name.rsplit(".", 1)[-1]
            if on_import_error is ImportErrorPolicy.AUTO and (
                _PLATFORM_RE.search(full_name)
                or _PLATFORM_PREFIX_RE.search(last_part)
            ):
                return None
            raise PakerDumpError(f"import {full_name}: {e}") from e
        return _dump(sub_module, skip_modules, options, on_import_error)

    # Plain module — pack bytecode from the source file without executing.
    if spec.origin in (None, "built-in", "frozen"):
        # Built-in or frozen modules live inside the interpreter, not as a
        # file we can pack. Skip.
        _logger.debug("skipping %s (origin=%s)", full_name, spec.origin)
        return None
    ext, src = _pack_file(spec.origin, full_name, options)
    node = {
        "type": "module",
        "extension": ext,
        "code": src,
    }
    if options.key is not None:
        node["encrypted"] = True
    return node


def _pack_file(
    path: str,
    module_name: str,
    options: _DumpOptions,
) -> tuple[str, str]:
    """Read a source file, compile-with-hooks for .py, encrypt if keyed.

    Returns ``(extension, encoded_code)`` where ``encoded_code`` is a base64
    string of either the marshalled bytecode (for ``.py``) or the raw bytes
    (for native extensions), optionally encrypted when ``options.key`` is set.
    """
    extension = path.rsplit(".", 1)[-1]

    if extension == "py":
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        if options.compile:
            marshalled = _compile_source(source, path, module_name, options)
            extension = "pyc"
            if options.key is not None:
                from paker._crypto import encrypt
                code = encrypt(marshalled, options.key)
            else:
                code = base64.b64encode(marshalled).decode()
        else:
            transformed = _transform_source(source, path, module_name, options)
            if options.key is not None:
                from paker._crypto import encrypt
                code = encrypt(transformed.encode("utf-8"), options.key)
            else:
                code = transformed
        return extension, code

    if extension in ("pyc", "dll", "pyd", "so"):
        with open(path, "rb") as f:
            raw = f.read()
        if options.key is not None:
            from paker._crypto import encrypt
            code = encrypt(raw, options.key)
        else:
            code = base64.b64encode(raw).decode()
        return extension, code

    raise PakerDumpError(
        f"unknown module extension '.{extension}' "
        f"(expected py, pyc, dll, pyd, so)"
    )


_CODE_FILE_EXTS = frozenset({
    ".py", ".pyc", ".pyo", ".pyd", ".so", ".dll",
})


def _pack_native_libs(
    module: types.ModuleType,
    options: _DumpOptions,
) -> list[dict]:
    """Discover bundled native libraries and pack them into a list node.

    Walks the package tree per platform convention (macOS ``.dylibs/``,
    Linux ``<pkg>.libs/`` sibling, Windows ``*.libs/``) and returns a list
    of ``{name, code, deps, soname, layout_path}`` dicts ready to be
    serialized into the bundle's ``native_libs`` field. When a ``key`` is
    supplied, each lib's payload is AES-CTR + HMAC encrypted.

    Returns an empty list when the package has no bundled libs or
    ``include_native_libs=False``.
    """
    from paker._native import discover_native_libs

    discovered = discover_native_libs(module)
    if not discovered.libs:
        return []

    encrypt = None
    if options.key is not None:
        from paker._crypto import encrypt as _encrypt
        encrypt = _encrypt

    packed: list[dict] = []
    for lib in discovered.libs:
        if encrypt is not None:
            code = encrypt(lib.data, options.key)
            encrypted = True
        else:
            code = base64.b64encode(lib.data).decode("ascii")
            encrypted = False
        node: dict = {
            "name": lib.name,
            "code": code,
            "layout_path": lib.layout_path,
        }
        if encrypted:
            node["encrypted"] = True
        if lib.deps:
            node["deps"] = lib.deps
        if lib.soname:
            node["soname"] = lib.soname
        packed.append(node)
    return packed


def _pack_resources(
    module: types.ModuleType,
    options: _DumpOptions,
) -> dict[str, str]:
    """Collect non-code data files from a package directory.

    Walks ``module.__path__`` entries. Prunes ``__pycache__``, hidden dirs,
    and any subdirectory that is itself a Python (sub)package — those get
    their resources packed under their own bundle node recursively.

    Returns a ``{posix_relpath: encoded_bytes}`` map. ``encoded_bytes`` is
    base64 of the file bytes when no key is set, or the AES-CTR + HMAC
    encrypted blob when a key is present.
    """
    resources: dict[str, str] = {}
    seen: set[str] = set()
    encrypt = None
    if options.key is not None:
        from paker._crypto import encrypt as _encrypt
        encrypt = _encrypt

    for path_entry in module.__path__:
        if not os.path.isdir(path_entry):
            continue
        path_entry_abs = os.path.abspath(path_entry)
        for root, dirs, files in os.walk(path_entry_abs):
            # Prune ``__pycache__``, dotfiles, and nested Python sub-packages
            # (they're handled by the ``_dump`` recursion).
            dirs[:] = [
                d for d in dirs
                if d != "__pycache__"
                and not d.startswith(".")
                and not os.path.isfile(os.path.join(root, d, "__init__.py"))
            ]
            dirs.sort()
            for name in sorted(files):
                if name.startswith("."):
                    continue
                _, ext = os.path.splitext(name)
                if ext.lower() in _CODE_FILE_EXTS:
                    continue
                src = os.path.join(root, name)
                rel = os.path.relpath(src, path_entry_abs).replace(os.sep, "/")
                if rel in seen:
                    continue
                seen.add(rel)
                try:
                    with open(src, "rb") as f:
                        raw = f.read()
                except OSError as e:
                    _logger.debug(
                        "skipping resource %s/%s: %s",
                        module.__name__, rel, e,
                    )
                    continue
                if encrypt is not None:
                    resources[rel] = encrypt(raw, options.key)
                else:
                    resources[rel] = base64.b64encode(raw).decode("ascii")
    return resources


def _compile_source(
    source: str,
    path: str,
    module_name: str,
    options: _DumpOptions,
) -> bytes:
    """Parse → (ast_transform) → compile(O2) → (code_transform) → (metadata scrub) → marshal."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        raise PakerDumpError(
            f"failed to parse {module_name} ({path}): {e}"
        ) from e

    if options.strip_annotations:
        tree = _StripAnnotations().visit(tree)
        ast.fix_missing_locations(tree)

    if options.ast_transform is not None:
        tree = options.ast_transform(tree, module_name)
        if not isinstance(tree, ast.AST):
            raise PakerDumpError(
                f"ast_transform for {module_name} returned "
                f"{type(tree).__name__}, expected ast.AST"
            )
        ast.fix_missing_locations(tree)

    try:
        code = compile(tree, path, "exec", optimize=2)
    except SyntaxError as e:
        raise PakerDumpError(
            f"failed to compile {module_name} ({path}): {e}"
        ) from e

    if options.code_transform is not None:
        code = options.code_transform(code, module_name)
        if not isinstance(code, types.CodeType):
            raise PakerDumpError(
                f"code_transform for {module_name} returned "
                f"{type(code).__name__}, expected types.CodeType"
            )

    # Always rewrite co_filename to avoid leaking the packager's filesystem.
    # strip_metadata=True  → blank everything (maximum stealth)
    # strip_metadata=False → paker:// virtual paths (useful tracebacks)
    code = _scrub_code_metadata(code, module_name, options.strip_metadata)

    return marshal.dumps(code)


def _transform_source(
    source: str,
    path: str,
    module_name: str,
    options: _DumpOptions,
) -> str:
    """Parse → (ast_transform) → ast.unparse. Returns portable source text."""
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        raise PakerDumpError(
            f"failed to parse {module_name} ({path}): {e}"
        ) from e

    if options.strip_annotations:
        tree = _StripAnnotations().visit(tree)
        ast.fix_missing_locations(tree)

    if options.ast_transform is not None:
        tree = options.ast_transform(tree, module_name)
        if not isinstance(tree, ast.AST):
            raise PakerDumpError(
                f"ast_transform for {module_name} returned "
                f"{type(tree).__name__}, expected ast.AST"
            )
        ast.fix_missing_locations(tree)

    return ast.unparse(tree)


def _scrub_code_metadata(
    code: types.CodeType,
    module_name: str,
    strip: bool,
    top_level: bool = True,
) -> types.CodeType:
    """Recursively rewrite filenames in a code object tree.

    When ``strip=True``, ``co_filename`` and nested ``co_qualname`` are
    blanked entirely — maximum stealth for production bundles.

    When ``strip=False``, ``co_filename`` is set to a ``paker://`` virtual
    path so tracebacks identify the failing module without leaking the
    packager's local filesystem paths.
    """
    if strip:
        new_filename = ""
    else:
        new_filename = (
            f"paker://{module_name.replace('.', '/')}.py"
            if module_name else ""
        )

    new_consts = []
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            new_consts.append(
                _scrub_code_metadata(const, module_name, strip, top_level=False),
            )
        else:
            new_consts.append(const)

    replacements = {
        "co_filename": new_filename,
        "co_consts": tuple(new_consts),
    }
    if strip and not top_level and hasattr(code, "co_qualname"):
        replacements["co_qualname"] = ""

    return code.replace(**replacements)


class _StripAnnotations(ast.NodeTransformer):
    """Remove type annotations and assert statements from a module tree.

    Opt-in via ``strip_annotations=True`` on :func:`dumps`. Destroys runtime
    type information used by pydantic / FastAPI / dataclasses.
    """

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        for arg_list in (node.args.args, node.args.posonlyargs, node.args.kwonlyargs):
            for arg in arg_list:
                arg.annotation = None
        if node.args.vararg is not None:
            node.args.vararg.annotation = None
        if node.args.kwarg is not None:
            node.args.kwarg.annotation = None
        node.returns = None
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_AnnAssign(self, node):
        # Keep the assignment when it has a value, drop the annotation;
        # otherwise remove the bare declaration entirely (no runtime effect).
        if node.value is None:
            return None
        return ast.Assign(
            targets=[node.target],
            value=node.value,
            type_comment=None,
        )

    def visit_Assert(self, node):
        return None
