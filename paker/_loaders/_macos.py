import os
import logging
import shutil
import subprocess
import tempfile
import uuid

from paker._loaders._base import NativeLoader, DataResolver, _load_dynamic

logger = logging.getLogger(__name__)


def _try_codesign(filepath: str) -> bool:
    """Apply ad-hoc code signature. Returns True on success."""
    try:
        result = subprocess.run(
            ["codesign", "-s", "-", "-f", filepath],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class UnlinkLoader(NativeLoader):
    """Loads native extensions via write → dlopen → unlink on macOS.

    When the extension has no bundled ``.dylibs/`` dependencies it's written
    to a single random-named file in ``$TMPDIR`` and unlinked immediately
    after ``dlopen``. File exists for milliseconds.

    When the caller provides ``bundled_libs`` (delocate-style ``.dylibs/``
    layout), the loader creates a private tempdir, recreates the package's
    directory tree (main ``.so`` + ``.dylibs/`` subdir with each bundled
    dylib at its expected ``@loader_path`` relative path), calls ``dlopen``
    with ``RTLD_NOW`` (forces full page-in so the subsequent ``unlink`` is
    safe), then walks the tree and unlinks every file plus the directories.
    POSIX semantics keep the mmap-backed pages valid until ``dlclose``.

    On Apple Silicon, most Python builds have the
    ``com.apple.security.cs.disable-library-validation`` entitlement so
    ad-hoc codesigning is usually unnecessary. The loader tries to load
    first and only falls back to ``codesign`` if the kernel rejects the
    binary.
    """

    supports_fd = True

    def __init__(self):
        self._loaded: dict[str, object] = {}

    @property
    def name(self) -> str:
        return "unlink"

    def load(
        self,
        fullname: str,
        data: bytes,
        initfuncname: str,
        spec,
        resolve_dependency: DataResolver | None = None,
        bundled_libs: list[tuple[str, bytes]] | None = None,
    ) -> object:
        return self._load_with_writer(
            fullname,
            lambda fd: os.write(fd, data),
            bundled_libs=bundled_libs,
        )

    def load_from_fd(
        self,
        fullname: str,
        write_fn,
        byte_count: int,
        initfuncname: str,
        spec,
        resolve_dependency=None,
        bundled_libs: list[tuple[str, bytes]] | None = None,
    ) -> object:
        return self._load_with_writer(
            fullname, write_fn, bundled_libs=bundled_libs,
        )

    def _load_with_writer(
        self,
        fullname,
        writer,
        *,
        bundled_libs: list[tuple[str, bytes]] | None,
    ) -> object:
        if fullname in self._loaded:
            return self._loaded[fullname]

        if not bundled_libs:
            return self._load_flat(fullname, writer)
        return self._load_with_sibling_tree(fullname, writer, bundled_libs)

    def _load_flat(self, fullname, writer) -> object:
        """No bundled deps — single tempfile, dlopen, unlink."""
        tmpdir = tempfile.gettempdir()
        filepath = os.path.join(tmpdir, uuid.uuid4().hex[:16])

        fd = os.open(filepath, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o700)
        try:
            writer(fd)
        finally:
            os.close(fd)

        try:
            module = _dlopen_with_codesign_fallback(fullname, filepath)
        except Exception:
            if os.path.exists(filepath):
                os.unlink(filepath)
            raise

        os.unlink(filepath)
        self._loaded[fullname] = module
        return module

    def _load_with_sibling_tree(
        self,
        fullname,
        writer,
        bundled_libs: list[tuple[str, bytes]],
    ) -> object:
        """Bundled deps present — recreate the full tree, dlopen, walk-unlink.

        ``bundled_libs`` is a list of ``(layout_path, raw_bytes)`` pairs.
        ``layout_path`` is relative to the main ``.so``'s directory (e.g.
        ``./.dylibs/libtiff.6.dylib``).
        """
        root = tempfile.mkdtemp(prefix="paker-mac-")
        main_path = os.path.join(root, uuid.uuid4().hex[:16])
        try:
            fd = os.open(main_path, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o700)
            try:
                writer(fd)
            finally:
                os.close(fd)

            # Materialize each bundled lib at its expected relative location.
            for layout_path, raw in bundled_libs:
                norm = layout_path[2:] if layout_path.startswith("./") else layout_path
                dest = os.path.join(root, norm)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as f:
                    f.write(raw)

            module = _dlopen_with_codesign_fallback(fullname, main_path)
        except Exception:
            # On load failure, tear the whole tree down before re-raising.
            shutil.rmtree(root, ignore_errors=True)
            raise

        # ``dlopen(RTLD_NOW)`` inside ``_load_dynamic`` has paged in the full
        # dependency graph. POSIX mmap guarantees the mappings survive the
        # subsequent file removal for the lifetime of the process.
        shutil.rmtree(root, ignore_errors=True)
        self._loaded[fullname] = module
        return module

    def cleanup(self) -> None:
        self._loaded.clear()


def _dlopen_with_codesign_fallback(fullname: str, filepath: str):
    """Call ``_load_dynamic``; if Apple Silicon rejects the binary, codesign
    and retry. Keeps the main-path / sibling-tree paths identical.
    """
    try:
        return _load_dynamic(fullname, filepath)
    except (ImportError, OSError):
        if not _try_codesign(filepath):
            raise
        return _load_dynamic(fullname, filepath)
