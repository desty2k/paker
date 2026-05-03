import ctypes
import logging
import os
import sys
import atexit
import uuid

from paker._loaders._base import NativeLoader, DataResolver, _load_dynamic, _has_static_tls

logger = logging.getLogger(__name__)

import _memimporter  # noqa: E402 — Windows-only C extension

# Win32 constants
_FILE_ATTRIBUTE_TEMPORARY = 0x100
_FILE_ATTRIBUTE_NOT_CONTENT_INDEXED = 0x2000
_MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x1
_CREATE_NEW = 1
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_kernel32 = ctypes.windll.kernel32

_CreateFileW = _kernel32.CreateFileW
_CreateFileW.restype = ctypes.c_void_p
_CreateFileW.argtypes = [
    ctypes.c_wchar_p,   # lpFileName
    ctypes.c_uint32,    # dwDesiredAccess
    ctypes.c_uint32,    # dwShareMode
    ctypes.c_void_p,    # lpSecurityAttributes
    ctypes.c_uint32,    # dwCreationDisposition
    ctypes.c_uint32,    # dwFlagsAndAttributes
    ctypes.c_void_p,    # hTemplateFile
]

_WriteFile = _kernel32.WriteFile
_WriteFile.restype = ctypes.c_int32
_WriteFile.argtypes = [
    ctypes.c_void_p,                 # hFile
    ctypes.c_void_p,                 # lpBuffer
    ctypes.c_uint32,                 # nNumberOfBytesToWrite
    ctypes.POINTER(ctypes.c_uint32), # lpNumberOfBytesWritten
    ctypes.c_void_p,                 # lpOverlapped
]

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.restype = ctypes.c_int32
_CloseHandle.argtypes = [ctypes.c_void_p]

_MoveFileExW = _kernel32.MoveFileExW
_MoveFileExW.restype = ctypes.c_int32
_MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]


def _get_cache_dir() -> str:
    """Return a cache directory for temporary native extensions."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or os.getcwd()
    path = os.path.join(base, "pyextcache")
    os.makedirs(path, exist_ok=True)
    return path


def _write_temp(path: str, data: bytes) -> None:
    """Write bytes via CreateFileW with TEMPORARY + NOT_CONTENT_INDEXED."""
    handle = _CreateFileW(
        path,
        _GENERIC_WRITE,
        _FILE_SHARE_READ,
        None,
        _CREATE_NEW,
        _FILE_ATTRIBUTE_TEMPORARY | _FILE_ATTRIBUTE_NOT_CONTENT_INDEXED,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE:
        raise OSError(f"CreateFileW failed for {path}, error {ctypes.GetLastError()}")
    try:
        written = ctypes.c_uint32(0)
        buf = (ctypes.c_char * len(data)).from_buffer_copy(data)
        if not _WriteFile(handle, buf, len(data), ctypes.byref(written), None):
            raise OSError(f"WriteFile failed, error {ctypes.GetLastError()}")
    finally:
        _CloseHandle(handle)


def _schedule_reboot_delete(path: str) -> None:
    """Schedule file deletion at next reboot via MoveFileExW."""
    _MoveFileExW(path, None, _MOVEFILE_DELAY_UNTIL_REBOOT)


def _sweep_stale(directory: str, max_age_seconds: float = 86400) -> None:
    """Remove stale files left by previous runs that crashed before cleanup."""
    import time
    try:
        now = time.time()
        for entry in os.scandir(directory):
            if not entry.is_file():
                continue
            try:
                age = now - entry.stat().st_mtime
                if age > max_age_seconds:
                    os.unlink(entry.path)
            except OSError:
                pass
    except OSError:
        pass


class MemImporterLoader(NativeLoader):
    """In-memory PE loader for Windows (.pyd/.dll).

    Extensions with Static TLS fall back to a tempfile + LoadLibrary.
    """

    def __init__(self):
        self._cache: dict[str, object] = {}
        self._bundled: dict[str, bytes] = {}
        self._tempfiles: list[str] = []
        self._cache_dir: str | None = None
        self._cleanup_registered = False

    @property
    def name(self) -> str:
        return "memimporter"

    def load(
        self,
        fullname: str,
        data: bytes,
        initfuncname: str,
        spec,
        resolve_dependency: DataResolver | None = None,
        bundled_libs: list | None = None,
    ) -> object:
        if fullname in self._cache:
            return self._cache[fullname]

        if _has_static_tls(data):
            logger.debug(
                "%s has Static TLS, falling back to tempfile loader", fullname,
            )
            return self._load_via_tempfile(fullname, data, initfuncname, spec)

        if bundled_libs:
            for layout_path, raw in bundled_libs:
                basename = layout_path.rsplit("/", 1)[-1].lower()
                self._bundled[basename] = raw

        pathname = fullname.replace(".", "\\") + ".pyd"

        def finder(name):
            if name == pathname:
                return data
            result = self._bundled.get(name.lower())
            if result is not None:
                return result
            if resolve_dependency is not None:
                result = resolve_dependency(name)
                if result is not None:
                    return result
            return None

        module = _memimporter.import_module(
            fullname, pathname, initfuncname, finder, spec,
        )
        module.__name__ = fullname
        self._cache[fullname] = module
        return module

    def _ensure_cache_dir(self) -> str:
        if self._cache_dir is not None and os.path.isdir(self._cache_dir):
            return self._cache_dir
        self._cache_dir = _get_cache_dir()
        _sweep_stale(self._cache_dir)
        if not self._cleanup_registered:
            atexit.register(self.cleanup)
            self._cleanup_registered = True
        return self._cache_dir

    def _load_via_tempfile(
        self, fullname: str, data: bytes, initfuncname: str, spec,
    ) -> object:
        """Write to a temp cache file and use native LoadLibrary."""
        cache_dir = self._ensure_cache_dir()
        filename = uuid.uuid4().hex[:16] + ".pyd"
        path = os.path.join(cache_dir, filename)

        _write_temp(path, data)
        _schedule_reboot_delete(path)

        module = _load_dynamic(fullname, path)
        self._cache[fullname] = module
        self._tempfiles.append(path)
        return module

    def cleanup(self) -> None:
        self._cache.clear()
        self._bundled.clear()
        for path in self._tempfiles:
            try:
                with open(path, "wb") as f:
                    f.write(b"\x00" * os.path.getsize(path))
            except OSError:
                pass
            try:
                os.unlink(path)
            except OSError:
                pass
        self._tempfiles.clear()
