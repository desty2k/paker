"""PEP 451 meta path finder + loader for paker bundles."""

from __future__ import annotations

import base64
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import logging
import marshal
import sys

from paker._loaders import NativeLoader, get_native_loader
from paker._loaders._resource_paths import AsFileStrategy, resolve_strategy
from paker.exceptions import PakerImportError


import email.message
import importlib.metadata

_module_type = type(sys)


def _normalize_dist_name(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


class _BundleDistribution(importlib.metadata.Distribution):
    """Minimal distribution backed by a paker bundle node."""

    def __init__(self, name: str, version: str):
        self._name = name
        self._version = version

    def read_text(self, filename: str) -> str | None:
        if filename == "METADATA":
            return f"Metadata-Version: 2.1\nName: {self._name}\nVersion: {self._version}\n"
        return None

    def locate_file(self, path):
        return None


class JsonImporter(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """PEP 451 meta path finder / loader that imports modules from a bundle dict.

    When ``key`` is provided, each module's encrypted code is decrypted lazily
    on import via the ``_paker_crypto`` C extension (AES-256-CTR + HMAC-SHA256).
    Native extensions load via platform-specific stealth paths — ``memfd`` on
    Linux, in-memory PE mapping on Windows, ``write → dlopen → unlink`` on
    macOS. The key is copied into a mutable bytearray at construction and
    zeroed in :meth:`unload` so it doesn't linger in the Python heap.
    """

    def __init__(
        self,
        modules: dict,
        native_loader: NativeLoader | None = None,
        key: bytes | None = None,
        as_file_strategy: AsFileStrategy | str | None = None,
        virtual_fs: bool = True,
    ):
        self._modules = dict(modules)
        self._native_loader = native_loader or get_native_loader()
        # Mutable copy so ``unload()`` can zero it in place. The caller's
        # ``bytes`` object would be immutable.
        self._key: bytearray | None = bytearray(key) if key is not None else None
        self._as_file_strategy: AsFileStrategy = resolve_strategy(as_file_strategy)
        self._owns_virtual_fs = False
        self._module_cache: dict[str, object] = {}
        self._logger = logging.getLogger(self.__class__.__name__)
        self._context_stack: list[str] = []
        sys.meta_path.insert(0, self)
        if virtual_fs:
            from paker.importers._virtual_fs import _install
            _install()
            self._owns_virtual_fs = True

    # -- MetaPathFinder (PEP 451) --

    def find_spec(self, fullname, path, target=None):
        node = self._resolve(fullname)
        if node is None:
            return None

        is_package = node.get("type") == "package"
        origin = "paker://" + fullname

        spec = importlib.machinery.ModuleSpec(
            name=fullname,
            loader=self,
            origin=origin,
            is_package=is_package,
        )
        if is_package:
            spec.submodule_search_locations = [origin]
        return spec

    # -- Loader (PEP 451) --

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        fullname = module.__name__
        node = self._resolve(fullname)
        if node is None:
            raise PakerImportError(f"cannot find {fullname} in paker modules")

        extension = node["extension"]
        code = node["code"]
        encrypted = node.get("encrypted", False)

        # Many libraries reference ``__file__`` at module-body level to
        # locate data files (boto3, certifi, jinja2 templates, ...). Paker
        # loads modules from memory, but picks a ``__file__`` that survives
        # ``os.path.dirname`` + ``os.path.join`` round-trips so callers'
        # path computations produce something meaningful that the virtual
        # FS shim (``paker.virtual_fs()``) can route back to bundle data.
        #
        # Uses slash-separated on-disk layout (``botocore.data`` →
        # ``paker://botocore/data``), not the dotted-name origin. That way
        # ``os.path.dirname(module.__file__)`` yields the package's
        # directory and ``os.path.join(that, 'x.json')`` yields the same
        # POSIX relpath layout the packer stored.
        is_package = node.get("type") == "package"
        path_segment = fullname.replace(".", "/")
        if is_package:
            module.__file__ = f"paker://{path_segment}/__init__.py"
        else:
            module.__file__ = f"paker://{path_segment}.py"

        if extension == "py":
            if not hasattr(module, "__builtins__"):
                module.__builtins__ = __builtins__
            if not code:
                # Namespace package — no __init__.py, nothing to execute.
                pass
            elif encrypted and self._key is not None:
                from paker._crypto import decrypt_to_code
                code_obj = decrypt_to_code(code, self._key, module.__file__)
                exec(code_obj, module.__dict__)
            else:
                exec(
                    compile(code, module.__file__, "exec"),
                    module.__dict__,
                )

        elif extension == "pyc":
            if not hasattr(module, "__builtins__"):
                module.__builtins__ = __builtins__
            if encrypted and self._key is not None:
                from paker._crypto import decrypt_to_unmarshalled
                bytecode = decrypt_to_unmarshalled(code, self._key)
            else:
                bytecode = marshal.loads(base64.b64decode(code))
            exec(bytecode, module.__dict__)

        elif extension in ("dll", "pyd", "so"):
            initfuncname = "PyInit_" + fullname.split(".")[-1]
            spec = module.__spec__
            bundled_libs = self._decode_bundled_libs(fullname)
            loaded = self._load_native(
                fullname, code, encrypted, initfuncname, spec,
                bundled_libs=bundled_libs,
            )
            module.__dict__.update(loaded.__dict__)

        else:
            raise PakerImportError(
                f"unsupported extension '.{extension}' for {fullname} "
                f"(expected py, pyc, dll, pyd, so)"
            )

        if encrypted:
            # Stop the bundle from retaining the ciphertext for this module
            # once it's been decrypted and executed. Re-imports rely on
            # ``sys.modules`` caching; the loader itself won't be asked to
            # decrypt this module again.
            node["code"] = None
        self._module_cache[fullname] = module
        self._logger.info("%s imported successfully", fullname)

    def _load_native(
        self,
        fullname: str,
        code: str,
        encrypted: bool,
        initfuncname: str,
        spec,
        bundled_libs: list[tuple[str, bytes]] | None = None,
    ) -> object:
        """Dispatch native-extension loading.

        When the module is encrypted and the loader supports fd I/O (Linux
        memfd, macOS tempfile), delegate to ``decrypt_to_fd`` — plaintext is
        written straight to the loader's fd, never materialising as a Python
        object. Falls back to decrypting into a caller-scrubbed bytearray on
        Windows (``_memimporter`` requires plaintext ``bytes``) and for
        plaintext bundles (``encrypted=False``).

        ``bundled_libs`` carries the parent package's ``native_libs`` (raw
        bytes + per-library layout path) so platform loaders that need a
        sibling filesystem layout (macOS delocate, Linux ``$ORIGIN``
        rpath fallback, Windows ``delvewheel``) can recreate it.
        """
        kwargs = {"resolve_dependency": self._get_native_data}
        if bundled_libs:
            kwargs["bundled_libs"] = bundled_libs

        if encrypted and self._key is not None and self._native_loader.supports_fd:
            from paker._crypto import decrypt_to_fd

            def write_payload(fd):
                decrypt_to_fd(code, self._key, fd)

            return self._native_loader.load_from_fd(
                fullname, write_payload, 0, initfuncname, spec, **kwargs,
            )

        if encrypted and self._key is not None:
            data = self._decrypt_native_to_bytes(code)
        else:
            data = base64.b64decode(code)

        return self._native_loader.load(
            fullname, data, initfuncname, spec, **kwargs,
        )

    def _decode_bundled_libs(
        self, fullname: str,
    ) -> list[tuple[str, bytes]] | None:
        """Return the parent package's ``native_libs`` as ``(layout, bytes)`` pairs.

        ``fullname`` is a native extension's dotted name (e.g.
        ``PIL._imaging``). Its ``native_libs`` live on the nearest ancestor
        package node. Returns ``None`` when no libs were bundled.
        """
        parent_parts = fullname.split(".")[:-1]
        if not parent_parts:
            return None
        node = self._resolve(".".join(parent_parts))
        if node is None:
            return None
        libs = node.get("native_libs")
        if not libs:
            return None
        result: list[tuple[str, bytes]] = []
        for lib in libs:
            raw = self._decode_native_lib_blob(lib)
            result.append((lib["layout_path"], raw))
        return result

    def _decode_native_lib_blob(self, lib: dict) -> bytes:
        """Decode (and decrypt, if needed) a single bundled-lib entry."""
        code = lib["code"]
        if lib.get("encrypted") and self._key is not None:
            return self._decrypt_native_to_bytes(code)
        return base64.b64decode(code)

    def _get_native_data(self, pathname: str) -> bytes | None:
        """Resolve a native dependency pathname to raw bytes.

        Used by the Windows ``_memimporter`` dependency-resolution callback.
        Encrypted dependencies go through a scrubbed bytearray before we
        materialise ``bytes`` so a memory scanner sees the buffer zeroed
        immediately after.
        """
        clean = pathname.replace("\\", "/")
        parts = clean.replace("/", ".").rsplit(".", 1)[0].split(".")

        try:
            node = self._modules[parts[0]]
            for part in parts[1:]:
                node = node["modules"][part]
            code = node["code"]
            if node.get("encrypted") and self._key is not None:
                return self._decrypt_native_to_bytes(code)
            return base64.b64decode(code)
        except (KeyError, TypeError):
            return None

    def _decrypt_native_to_bytes(self, blob_b64: str) -> bytes:
        """Decrypt encrypted native data into a scrubbed bytearray → bytes.

        ``_memimporter`` requires plaintext ``bytes`` as input. We decrypt
        through the C extension's ``decrypt_into_bytearray`` (which never
        returns plaintext as a ``bytes`` object) and only materialise bytes
        at the last possible moment. The underlying bytearray is scrubbed
        after the copy.
        """
        from paker._crypto import (
            _aes_ctr_fallback,
            _c_decrypt_into_bytearray,
            _derive_keys,
            _validate_key,
            _verify_and_split,
            scrub,
        )

        _validate_key(self._key)
        enc_key, mac_key = _derive_keys(self._key)
        payload = _verify_and_split(blob_b64, mac_key)

        dest = bytearray()
        if _c_decrypt_into_bytearray is not None:
            _c_decrypt_into_bytearray(payload, enc_key, dest)
        else:
            dest.extend(
                _aes_ctr_fallback(enc_key, payload[:16], payload[16:])
            )

        try:
            return bytes(dest)
        finally:
            scrub(dest)

    def _resolve(self, fullname: str) -> dict | None:
        parts = fullname.split(".")
        try:
            node = self._modules[parts[0]]
            for part in parts[1:]:
                node = node["modules"][part]
            return node
        except (KeyError, TypeError):
            return None

    # -- Distribution metadata --

    def find_distributions(self, context=None):
        """Yield lightweight ``Distribution`` objects for bundled packages.

        Called by ``importlib.metadata.version()`` / ``.distribution()`` so
        packages that check their own version at import time (``mcp``,
        ``uvicorn``, etc.) don't raise ``PackageNotFoundError``.
        """
        from importlib.metadata import DistributionFinder
        if context is None:
            context = DistributionFinder.Context()
        name_filter = getattr(context, "name", None)
        for _top, node in self._modules.items():
            if not isinstance(node, dict):
                continue
            dist_name = node.get("dist_name")
            dist_version = node.get("dist_version")
            if not dist_name or not dist_version:
                continue
            if name_filter and _normalize_dist_name(name_filter) != _normalize_dist_name(dist_name):
                continue
            yield _BundleDistribution(dist_name, dist_version)

    # -- Resource support --

    def _resources_for(self, fullname: str) -> dict | None:
        """Return the ``{posix_relpath: blob}`` dict for a bundled package.

        ``None`` when ``fullname`` isn't in the bundle or isn't a package.
        Resources live alongside module code on package nodes; single-module
        nodes don't have them.
        """
        node = self._resolve(fullname)
        if node is None:
            return None
        if node.get("type") != "package":
            return None
        return node.get("resources")

    def get_resource_reader(self, fullname: str):
        """Modern ``importlib.resources`` entry point.

        ``importlib.resources.files(pkg)`` calls this to get a
        :class:`TraversableResources`; ``.files()`` on the returned reader
        then hands back a :class:`Traversable` rooted at the package.
        """
        if self._resolve(fullname) is None:
            return None
        from paker.importers._resources import BundleResources
        return BundleResources(self, fullname)

    def get_data(self, path: str) -> bytes:
        """Legacy ``pkgutil.get_data`` protocol.

        CPython synthesises ``path`` by joining ``os.path.dirname(module.__file__)``
        with the caller's relpath. paker sets ``module.__file__`` to
        ``paker://<dotted.name>``, so ``path`` looks like
        ``paker://<dotted.name>/<relpath>`` on POSIX hosts and may use
        backslashes on Windows. We decode by stripping the ``paker://``
        prefix and splitting on the first ``/`` to recover the package name
        and in-package relpath.
        """
        normalised = path.replace("\\", "/")
        prefix = "paker://"
        if normalised.startswith(prefix):
            normalised = normalised[len(prefix):]
        # ``normalised`` is now ``<dotted.name>/<relpath>``. The dotted name
        # can itself contain ``.`` so we find the longest matching package
        # prefix in the bundle.
        package_fullname, rel = self._split_resource_path(normalised)
        if package_fullname is None:
            raise FileNotFoundError(path)
        resources = self._resources_for(package_fullname)
        if resources is None or rel not in resources:
            raise FileNotFoundError(path)
        from paker.importers._resources import _decode_resource
        return _decode_resource(self, resources[rel])

    def _split_resource_path(self, joined: str) -> tuple[str | None, str]:
        """Split ``<pkg-with-possible-dots>/<resource-relpath>`` into its pieces.

        Walks path segments from the left, accumulating until the bundle's
        nested module tree no longer recognises the prefix as a package.
        """
        segments = joined.split("/")
        node_map = self._modules
        pkg_parts: list[str] = []
        idx = 0
        while idx < len(segments):
            seg = segments[idx]
            if seg in node_map:
                node = node_map[seg]
                if not isinstance(node, dict):
                    break
                pkg_parts.append(seg)
                if node.get("type") != "package":
                    idx += 1
                    break
                node_map = node.get("modules", {})
                idx += 1
                continue
            break
        if not pkg_parts:
            return None, joined
        rel = "/".join(segments[idx:])
        return ".".join(pkg_parts), rel

    # -- Legacy finder protocol --

    def find_module(self, fullname, path=None):
        return self if self._resolve(fullname) is not None else None

    def load_module(self, fullname):
        if fullname in sys.modules:
            return sys.modules[fullname]
        spec = self.find_spec(fullname, None)
        if spec is None:
            raise PakerImportError(f"cannot find {fullname}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = module
        spec.loader.exec_module(module)
        return module

    # -- Module management --

    def add_module(self, name: str, data: dict) -> None:
        if not isinstance(data, dict):
            raise PakerImportError(
                f"module data must be a dict (got {type(data).__name__})"
            )
        self._modules[name] = data
        self._context_stack.append(name)
        self._logger.info("%s added", name)

    def unload_module(self, name: str) -> None:
        if isinstance(name, _module_type):
            name = name.__name__
        self._modules.pop(name, None)
        self._module_cache.pop(name, None)
        sys.modules.pop(name, None)
        self._logger.info("%s unloaded", name)

    def unload(self) -> None:
        for name in list(self._module_cache):
            sys.modules.pop(name, None)
        self._module_cache.clear()
        self._modules.clear()
        self._context_stack.clear()
        if self in sys.meta_path:
            sys.meta_path.remove(self)
        self._native_loader.cleanup()
        if self._owns_virtual_fs:
            from paker.importers._virtual_fs import _uninstall
            _uninstall()
            self._owns_virtual_fs = False
        if self._key is not None:
            from paker._crypto import scrub
            scrub(self._key)
            self._key = None
        self._logger.info("unloaded all modules")

    # -- Context manager --

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if not self._context_stack:
            self.unload()
        else:
            self.unload_module(self._context_stack.pop())
