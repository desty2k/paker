import os
import ctypes
import ctypes.util
import atexit
import logging

from paker._loaders._base import NativeLoader, DataResolver, _load_dynamic

logger = logging.getLogger(__name__)

MFD_CLOEXEC = 0x0001

# ``dlopen`` flags — ``RTLD_NOW`` forces immediate symbol resolution (safer
# with memfd + unlinked sources); ``RTLD_GLOBAL`` exposes a library's symbols
# to subsequent ``dlopen`` calls, which is how we satisfy ``DT_NEEDED``
# lookups of bundled dependencies by soname without the dynamic linker
# walking ``$ORIGIN`` (which resolves to ``/`` for a ``/proc/self/fd/N`` path).
_RTLD_NOW = 0x2
_RTLD_GLOBAL = 0x100

_libc = None
_memfd_create = None
_dlopen = None
_dlerror = None


def _get_memfd_create():
    """Resolve memfd_create from libc. Raises OSError if unavailable."""
    global _libc, _memfd_create
    if _memfd_create is not None:
        return _memfd_create

    libc_name = ctypes.util.find_library("c")
    if libc_name is None:
        raise OSError("cannot find libc")
    _libc = ctypes.CDLL(libc_name, use_errno=True)

    try:
        _memfd_create = _libc.memfd_create
    except AttributeError:
        raise OSError("memfd_create not available (requires Linux >= 3.17)")

    _memfd_create.restype = ctypes.c_int
    _memfd_create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    return _memfd_create


def _get_dl_funcs():
    """Resolve ``dlopen`` / ``dlerror`` from libdl (or libc on glibc 2.34+)."""
    global _dlopen, _dlerror
    if _dlopen is not None and _dlerror is not None:
        return _dlopen, _dlerror

    dl_name = ctypes.util.find_library("dl")
    dl = ctypes.CDLL(dl_name, use_errno=True) if dl_name else _libc
    if dl is None:
        raise OSError("cannot find libdl/libc for dlopen")
    _dlopen = dl.dlopen
    _dlopen.restype = ctypes.c_void_p
    _dlopen.argtypes = [ctypes.c_char_p, ctypes.c_int]
    _dlerror = dl.dlerror
    _dlerror.restype = ctypes.c_char_p
    _dlerror.argtypes = []
    return _dlopen, _dlerror


class MemfdLoader(NativeLoader):
    """Loads native extensions via memfd_create + dlopen on Linux.

    Creates anonymous in-memory file descriptors, writes the .so bytes,
    and loads via /proc/self/fd/<n>. No files are written to disk.
    Requires Linux >= 3.17 and /proc mounted.

    When the extension ships bundled native dependencies (``<pkg>.libs/*.so``
    from auditwheel), :meth:`_preload_bundled_libs` memfd's each dep in
    topological order and ``dlopen(..., RTLD_GLOBAL)``s them, so the main
    extension's ``DT_NEEDED`` lookups find them by soname without
    ``$ORIGIN`` being consulted. Zero disk for the whole dependency graph.
    """

    supports_fd = True

    def __init__(self):
        self._memfd_create = _get_memfd_create()
        self._fds: dict[str, int] = {}
        self._loaded: dict[str, object] = {}
        # Bundled deps loaded via raw dlopen aren't Python modules — track
        # their fds so cleanup closes them at process exit.
        self._lib_fds: list[int] = []
        self._preloaded_sonames: set[str] = set()
        self._cleanup_registered = False

    @property
    def name(self) -> str:
        return "memfd"

    def load(
        self,
        fullname: str,
        data: bytes,
        initfuncname: str,
        spec,
        resolve_dependency: DataResolver | None = None,
        bundled_libs: list | None = None,
    ) -> object:
        if bundled_libs:
            self._preload_bundled_libs(bundled_libs)
        return self._load_with_writer(
            fullname, lambda fd: os.write(fd, data), spec, resolve_dependency
        )

    def load_from_fd(
        self,
        fullname: str,
        write_fn,
        byte_count: int,
        initfuncname: str,
        spec,
        resolve_dependency=None,
        bundled_libs: list | None = None,
    ) -> object:
        if bundled_libs:
            self._preload_bundled_libs(bundled_libs)
        return self._load_with_writer(fullname, write_fn, spec, resolve_dependency)

    def _load_with_writer(self, fullname, writer, spec, resolve_dependency):
        if fullname in self._loaded:
            return self._loaded[fullname]

        fd = self._memfd_create(b"", MFD_CLOEXEC)
        if fd < 0:
            errno = ctypes.get_errno()
            raise OSError(errno, f"memfd_create failed for {fullname}: {os.strerror(errno)}")

        try:
            writer(fd)
            path = f"/proc/self/fd/{fd}"
            module = _load_dynamic(fullname, path)
        except Exception:
            os.close(fd)
            raise

        self._fds[fullname] = fd
        self._loaded[fullname] = module

        if not self._cleanup_registered:
            atexit.register(self.cleanup)
            self._cleanup_registered = True

        return module

    def _preload_bundled_libs(self, bundled_libs: list) -> None:
        """Topologically sort bundled libs and dlopen them with RTLD_GLOBAL.

        ``bundled_libs`` is a list of ``(layout_path, raw_bytes)`` pairs.
        Each lib's ELF dependencies (``DT_NEEDED``) are parsed against the
        set of libs we know about, then the ready-set (no unresolved deps)
        is flushed first, recursing until all are loaded. Skips libs whose
        sonames are already loaded so repeated ``load()`` calls (different
        extensions in the same package) don't re-dlopen.
        """
        # Parse soname + needed for each bundled lib so we know the graph.
        from paker._native._elf import ElfParseError, parse_needed_and_soname

        by_name: dict[str, dict] = {}
        for layout_path, raw in bundled_libs:
            name = os.path.basename(layout_path)
            try:
                soname, needed = parse_needed_and_soname(raw)
            except ElfParseError as e:
                logger.debug("skipping bundled %s (ELF parse: %s)", name, e)
                continue
            by_name[soname or name] = {
                "name": name,
                "soname": soname or name,
                "needed": needed,
                "raw": raw,
            }

        # Topological sort: repeatedly flush libs whose needs are satisfied
        # by either a lib already loaded this session or a lib outside this
        # bundle (which the OS loader will resolve against the system).
        remaining = dict(by_name)
        while remaining:
            ready = [
                lib for lib in remaining.values()
                if all(
                    dep in self._preloaded_sonames
                    or dep not in by_name  # system lib — let the loader find it
                    for dep in lib["needed"]
                )
            ]
            if not ready:
                # Graph must have a cycle or unresolvable dep — emit the rest
                # in arbitrary order and let dlopen surface the real error.
                ready = list(remaining.values())
            for lib in ready:
                if lib["soname"] in self._preloaded_sonames:
                    remaining.pop(lib["soname"], None)
                    continue
                self._dlopen_from_memfd(lib["raw"], lib["name"])
                self._preloaded_sonames.add(lib["soname"])
                remaining.pop(lib["soname"], None)

    def _dlopen_from_memfd(self, data: bytes, debug_name: str) -> None:
        """memfd_create + write + raw dlopen(RTLD_NOW|RTLD_GLOBAL).

        Doesn't go through importlib — this isn't a Python extension, just
        a bundled shared library whose symbols need to exist in the global
        namespace before the next Python-extension ``dlopen`` runs.
        """
        dlopen, dlerror = _get_dl_funcs()
        fd = self._memfd_create(
            debug_name.encode("ascii", "replace"), MFD_CLOEXEC,
        )
        if fd < 0:
            errno = ctypes.get_errno()
            raise OSError(
                errno,
                f"memfd_create failed for {debug_name}: {os.strerror(errno)}",
            )
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                view = view[n:]
            path = f"/proc/self/fd/{fd}".encode("ascii")
            handle = dlopen(path, _RTLD_NOW | _RTLD_GLOBAL)
            if not handle:
                err = dlerror()
                raise OSError(
                    f"dlopen failed for {debug_name}: "
                    f"{err.decode('utf-8', 'replace') if err else 'unknown'}"
                )
        except Exception:
            os.close(fd)
            raise

        # Keep fd open for the lifetime of the process — dlopen holds a
        # reference to the mmap'd pages via the fd; closing it is safe
        # after dlopen returns but keeping it simplifies debugging via
        # /proc/self/maps.
        self._lib_fds.append(fd)

    def cleanup(self) -> None:
        self._loaded.clear()
        for name, fd in self._fds.items():
            try:
                os.close(fd)
            except OSError:
                pass
        self._fds.clear()
        for fd in self._lib_fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self._lib_fds.clear()
        self._preloaded_sonames.clear()
