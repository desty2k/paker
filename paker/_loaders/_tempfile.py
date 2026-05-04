import os
import sys
import atexit
import shutil
import logging
import tempfile
import uuid

from paker._loaders._base import NativeLoader, DataResolver, _load_dynamic

logger = logging.getLogger(__name__)


class TempFileLoader(NativeLoader):
    """Last-resort loader: writes native extensions to temp files.

    Used when no platform-specific in-memory loader is available.
    Files use random names without identifiable prefixes or extensions.

    On Windows, loaded DLLs cannot be deleted while the process holds
    them (kernel file lock). Cleanup happens at interpreter exit via
    atexit. On other platforms, files could be unlinked after load, but
    if you're hitting this loader on Linux/macOS it means the better
    loaders (memfd/unlink) failed, so we keep things simple.
    """

    def __init__(self):
        self._tempdir: str | None = None
        self._loaded: dict[str, tuple[str, object]] = {}
        self._bundled_libs_written: set[str] = set()
        self._cleanup_registered = False

    @property
    def name(self) -> str:
        return "tempfile"

    def _ensure_tempdir(self) -> str:
        if self._tempdir is not None and os.path.isdir(self._tempdir):
            return self._tempdir
        self._tempdir = tempfile.mkdtemp(prefix="~")
        if not self._cleanup_registered:
            atexit.register(self.cleanup)
            self._cleanup_registered = True
        return self._tempdir

    def load(
        self,
        fullname: str,
        data: bytes,
        initfuncname: str,
        spec,
        resolve_dependency: DataResolver | None = None,
        bundled_libs: list | None = None,
    ) -> object:
        if fullname in self._loaded:
            return self._loaded[fullname][1]

        tempdir = self._ensure_tempdir()
        parts = fullname.split(".")
        package_dir = os.path.join(tempdir, parts[0])
        module_parts = parts[:-1]
        module_dir = os.path.join(tempdir, *module_parts) if module_parts else tempdir
        os.makedirs(module_dir, exist_ok=True)
        filename = uuid.uuid4().hex[:16]
        if sys.platform == "win32":
            filename += ".pyd"
        filepath = os.path.join(module_dir, filename)

        if bundled_libs:
            # Drop each bundled dep alongside the main .so using its layout
            # path so the dynamic linker's rpath resolution finds them.
            for layout_path, raw in bundled_libs:
                dest = _resolve_layout_path(package_dir, layout_path, tempdir)
                if dest in self._bundled_libs_written:
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                fd = os.open(dest, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o700)
                try:
                    os.write(fd, raw)
                finally:
                    os.close(fd)
                self._bundled_libs_written.add(dest)

        fd = os.open(filepath, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o700)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

        module = _load_dynamic(fullname, filepath)
        self._loaded[fullname] = (filepath, module)
        return module

    def cleanup(self) -> None:
        self._loaded.clear()
        self._bundled_libs_written.clear()
        if self._tempdir and os.path.isdir(self._tempdir):
            try:
                shutil.rmtree(self._tempdir, ignore_errors=True)
            except OSError:
                pass
            self._tempdir = None


def _resolve_layout_path(origin_dir: str, layout_path: str, tempdir: str) -> str:
    """Resolve a bundled-lib layout path without escaping ``tempdir``."""
    dest = os.path.normpath(os.path.join(origin_dir, layout_path))
    if not dest.startswith(os.path.normpath(tempdir) + os.sep):
        raise ValueError(
            f"bundled native lib path escapes tempdir: {layout_path!r}"
        )
    return dest
