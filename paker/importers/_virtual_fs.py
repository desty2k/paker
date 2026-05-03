"""Virtual-FS shim so libraries doing raw ``open()`` / ``os.path``
operations on ``__file__``-relative paths resolve against paker-bundled
resources.

Motivation: modern APIs (``importlib.resources``, ``pkgutil.get_data``) are
served transparently by :mod:`paker.importers._resources`. Legacy code that
uses ``open(os.path.join(os.path.dirname(__file__), 'data.json'))`` — notably
``botocore``'s ``FileLoader`` — bypasses those APIs and hits ``builtins.open``
or ``os.path.isfile`` / ``os.listdir`` directly with a path like
``paker://botocore/data/x.json``. Plain stdlib has no idea what that means
and the library either silently skips the file or errors out.

The shim is installed automatically by ``JsonImporter.__init__`` when
``virtual_fs=True`` (the default) and removed by ``JsonImporter.unload()``.
The install is refcounted so multiple importers or explicit
``with paker.virtual_fs():`` contexts compose correctly. The shim chains
through any prior ``builtins.open`` patcher (gevent, eventlet, pytest) and
restores it on uninstall.

The shim wraps:

- ``builtins.open`` — ``rb`` / ``rt`` modes returning ``BytesIO`` /
  ``StringIO``. Write modes are rejected (bundle is read-only).
- ``os.path.isfile``, ``os.path.isdir``, ``os.path.exists``.
- ``os.listdir``, ``os.scandir``, ``os.stat``.

Everything else falls through to the real stdlib function. Any path that
doesn't start with ``paker://`` (or ``paker:/``) is routed to the real
implementation with zero overhead beyond a prefix check.

Limitations:

- Returned file-like objects are ``io.BytesIO`` / ``io.StringIO``; they do
  not expose a real OS file descriptor. Libraries that call ``.fileno()``
  (e.g. OpenSSL's ``load_verify_locations(cafile=...)`` at the C layer)
  will fail.
- Only the above functions are patched. If a library reaches into C
  extensions for direct file I/O (``fopen`` without Python's help), this
  shim can't help.
"""

from __future__ import annotations

import builtins
import contextlib
import ctypes
import errno
import io
import os
import os.path
import stat
import sys
import tempfile
import threading


_PAKER_PREFIXES = ("paker://", "paker:/")

_INSTALL_LOCK = threading.Lock()
_INSTALL_COUNT = 0
_SAVED: dict[str, object] = {}
# Native libs materialized for ctypes.CDLL. Kept alive (fd open / file on
# disk) until the shim is fully uninstalled so dlopen'd handles stay valid.
_MATERIALIZED_LIBS: dict[str, tuple[str, int | None]] = {}  # paker_path → (real_path, fd_or_None)


@contextlib.contextmanager
def virtual_fs():
    """Patch the read-side of ``builtins.open`` / ``os.path`` / ``os`` so
    paths starting with ``paker://`` resolve to in-memory bytes served by
    the active :class:`paker.importers.JsonImporter` instances.

    ::

        with paker.loads(bundle):
            with paker.virtual_fs():
                # boto3 / botocore FileLoader + os.path.isfile checks now
                # resolve against the bundle's ``resources`` dict.
                import boto3
                boto3.client("s3", region_name="us-east-1")

    Nesting is safe (refcounted). Concurrent threads are serialised on
    install / uninstall through a lock so the transition is consistent.
    Any path not starting with ``paker://`` is routed to the real stdlib
    function — the shim is free for non-bundle paths.
    """
    _install()
    try:
        yield
    finally:
        _uninstall()


def _install() -> None:
    global _INSTALL_COUNT
    with _INSTALL_LOCK:
        if _INSTALL_COUNT == 0:
            _SAVED["open"] = builtins.open
            _SAVED["os.path.isfile"] = os.path.isfile
            _SAVED["os.path.isdir"] = os.path.isdir
            _SAVED["os.path.exists"] = os.path.exists
            _SAVED["os.path.abspath"] = os.path.abspath
            _SAVED["os.path.realpath"] = os.path.realpath
            _SAVED["os.listdir"] = os.listdir
            _SAVED["os.scandir"] = os.scandir
            _SAVED["os.stat"] = os.stat

            builtins.open = _paker_open
            os.path.isfile = _paker_isfile
            os.path.isdir = _paker_isdir
            os.path.exists = _paker_exists
            os.path.abspath = _paker_abspath
            os.path.realpath = _paker_realpath
            os.listdir = _paker_listdir
            os.scandir = _paker_scandir
            os.stat = _paker_stat

            _SAVED["ctypes.CDLL.__init__"] = ctypes.CDLL.__init__
            ctypes.CDLL.__init__ = _paker_cdll_init
        _INSTALL_COUNT += 1


def _uninstall() -> None:
    global _INSTALL_COUNT
    with _INSTALL_LOCK:
        _INSTALL_COUNT -= 1
        if _INSTALL_COUNT <= 0:
            _INSTALL_COUNT = 0
            if _SAVED:
                builtins.open = _SAVED.pop("open")
                os.path.isfile = _SAVED.pop("os.path.isfile")
                os.path.isdir = _SAVED.pop("os.path.isdir")
                os.path.exists = _SAVED.pop("os.path.exists")
                os.path.abspath = _SAVED.pop("os.path.abspath")
                os.path.realpath = _SAVED.pop("os.path.realpath")
                os.listdir = _SAVED.pop("os.listdir")
                os.scandir = _SAVED.pop("os.scandir")
                os.stat = _SAVED.pop("os.stat")
                ctypes.CDLL.__init__ = _SAVED.pop("ctypes.CDLL.__init__")
                _cleanup_materialized_libs()


# ---------------------------------------------------------------------------
# Path recognition + bundle resolution
# ---------------------------------------------------------------------------


def _path_str(value) -> str | None:
    """Extract a string path from a value that might be str / bytes / os.PathLike."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode(errors="surrogateescape")
    if hasattr(value, "__fspath__"):
        return _path_str(value.__fspath__())
    return None


def _is_paker_path(value) -> str | None:
    """Return the string form of ``value`` when it's a paker path, else None."""
    s = _path_str(value)
    if s is None:
        return None
    normalised = s.replace("\\", "/")
    if any(normalised.startswith(pfx) for pfx in _PAKER_PREFIXES):
        return normalised
    return None


def _strip_prefix(normalised: str) -> str:
    for pfx in _PAKER_PREFIXES:
        if normalised.startswith(pfx):
            return normalised[len(pfx):]
    return normalised


def _iter_importers():
    """Yield every :class:`JsonImporter` currently installed on ``sys.meta_path``."""
    from paker.importers import JsonImporter
    for finder in sys.meta_path:
        if isinstance(finder, JsonImporter):
            yield finder


def _lookup_bytes(normalised: str) -> bytes | None:
    """Resolve ``normalised`` (``paker://<pkg>/<relpath>``) against active importers.

    Returns decoded bytes on a hit, ``None`` on miss. Short-circuits to
    ``None`` when the path points at a package ``__init__.py`` (module code
    isn't served as resource bytes; callers that want that should use
    ``importlib.util`` instead).
    """
    stripped = _strip_prefix(normalised)
    for importer in _iter_importers():
        pkg_fullname, rel = importer._split_resource_path(stripped)
        if pkg_fullname is None:
            continue
        if rel in ("", "__init__.py"):
            return None
        resources = importer._resources_for(pkg_fullname)
        if resources is None or rel not in resources:
            continue
        from paker.importers._resources import _decode_resource
        return _decode_resource(importer, resources[rel])
    return None


def _path_exists(normalised: str) -> tuple[bool, bool]:
    """Return ``(exists, is_dir)`` for ``normalised``.

    Existence covers both "a resource at this exact path" and "a virtual
    directory implied by resources under this prefix". Roots
    (``paker://pkg`` for any packaged pkg) always exist as directories.
    """
    stripped = _strip_prefix(normalised).rstrip("/")
    for importer in _iter_importers():
        pkg_fullname, rel = importer._split_resource_path(stripped)
        if pkg_fullname is None:
            continue
        # Package root — always a directory.
        if rel in ("", "__init__.py") if rel != "__init__.py" else False:
            return True, True
        if rel == "":
            return True, True
        resources = importer._resources_for(pkg_fullname)
        if resources is None:
            continue
        if rel in resources:
            return True, False
        prefix = rel + "/"
        if any(k.startswith(prefix) for k in resources):
            return True, True
    return False, False


def _iter_directory(normalised: str):
    """Yield (name, is_file) entries for the virtual directory at ``normalised``."""
    stripped = _strip_prefix(normalised).rstrip("/")
    for importer in _iter_importers():
        pkg_fullname, rel = importer._split_resource_path(stripped)
        if pkg_fullname is None:
            continue
        resources = importer._resources_for(pkg_fullname)
        if resources is None:
            continue
        prefix = (rel + "/") if rel else ""
        seen: set[str] = set()
        for key in resources:
            if not key.startswith(prefix):
                continue
            tail = key[len(prefix):]
            if not tail:
                continue
            head, sep, _ = tail.partition("/")
            if head in seen:
                continue
            seen.add(head)
            is_file = (sep == "")
            yield head, is_file
        return
    # No importer recognised this root → empty dir.


# ---------------------------------------------------------------------------
# Shim implementations
# ---------------------------------------------------------------------------


def _paker_open(
    file,
    mode: str = "r",
    buffering: int = -1,
    encoding: str | None = None,
    errors_: str | None = None,
    newline: str | None = None,
    closefd: bool = True,
    opener=None,
):
    normalised = _is_paker_path(file)
    if normalised is None:
        return _SAVED["open"](
            file, mode, buffering, encoding, errors_, newline, closefd, opener,
        )
    if any(c in mode for c in "wxa+"):
        raise PermissionError(
            errno.EACCES,
            "paker bundle resources are read-only",
            _path_str(file),
        )
    data = _lookup_bytes(normalised)
    if data is None:
        raise FileNotFoundError(
            errno.ENOENT, "No such file or directory", _path_str(file),
        )
    if "b" in mode:
        return io.BytesIO(data)
    enc = encoding or "utf-8"
    return io.StringIO(data.decode(enc, errors_ or "strict"))


def _paker_isfile(path) -> bool:
    normalised = _is_paker_path(path)
    if normalised is None:
        return _SAVED["os.path.isfile"](path)
    exists, is_dir = _path_exists(normalised)
    return exists and not is_dir


def _paker_isdir(path) -> bool:
    normalised = _is_paker_path(path)
    if normalised is None:
        return _SAVED["os.path.isdir"](path)
    exists, is_dir = _path_exists(normalised)
    return exists and is_dir


def _paker_exists(path) -> bool:
    normalised = _is_paker_path(path)
    if normalised is None:
        return _SAVED["os.path.exists"](path)
    exists, _is_dir = _path_exists(normalised)
    return exists


def _paker_listdir(path=".") -> list[str]:
    normalised = _is_paker_path(path)
    if normalised is None:
        return _SAVED["os.listdir"](path)
    exists, is_dir = _path_exists(normalised)
    if not exists:
        raise FileNotFoundError(
            errno.ENOENT, "No such file or directory", _path_str(path),
        )
    if not is_dir:
        raise NotADirectoryError(
            errno.ENOTDIR, "Not a directory", _path_str(path),
        )
    return [name for name, _is_file in _iter_directory(normalised)]


class _PakerDirEntry:
    """Minimal ``os.DirEntry`` stand-in backed by the bundle."""

    __slots__ = ("_name", "_path", "_is_file")

    def __init__(self, name: str, parent_path: str, is_file: bool):
        self._name = name
        self._path = parent_path.rstrip("/") + "/" + name
        self._is_file = is_file

    @property
    def name(self) -> str:
        return self._name

    @property
    def path(self) -> str:
        return self._path

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        return self._is_file

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        return not self._is_file

    def is_symlink(self) -> bool:
        return False

    def inode(self) -> int:
        return 0

    def stat(self, *, follow_symlinks: bool = True):
        return _make_stat(self._is_file)

    def __fspath__(self) -> str:
        return self._path


class _PakerScandirIterator:
    """Iterator returned by ``os.scandir`` for a paker path. Context-manager compatible."""

    def __init__(self, parent_path: str, entries: list[_PakerDirEntry]):
        self._parent_path = parent_path
        self._entries = iter(entries)

    def __iter__(self):
        return self

    def __next__(self) -> _PakerDirEntry:
        return next(self._entries)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def close(self) -> None:
        pass


def _paker_scandir(path="."):
    normalised = _is_paker_path(path)
    if normalised is None:
        return _SAVED["os.scandir"](path)
    exists, is_dir = _path_exists(normalised)
    if not exists:
        raise FileNotFoundError(
            errno.ENOENT, "No such file or directory", _path_str(path),
        )
    if not is_dir:
        raise NotADirectoryError(
            errno.ENOTDIR, "Not a directory", _path_str(path),
        )
    parent = _path_str(path)
    entries = [
        _PakerDirEntry(name, parent, is_file)
        for name, is_file in _iter_directory(normalised)
    ]
    return _PakerScandirIterator(parent, entries)


def _make_stat(is_file: bool):
    """Build a plausible ``os.stat_result`` for a virtual path."""
    mode = stat.S_IFREG | 0o444 if is_file else stat.S_IFDIR | 0o555
    # (mode, ino, dev, nlink, uid, gid, size, atime, mtime, ctime)
    return os.stat_result((mode, 0, 0, 1, 0, 0, 0, 0, 0, 0))


def _paker_abspath(path) -> str:
    normalised = _is_paker_path(path)
    if normalised is None:
        return _SAVED["os.path.abspath"](path)
    stripped = _strip_prefix(normalised)
    return "paker://" + stripped


def _paker_realpath(path, *, strict=False) -> str:
    normalised = _is_paker_path(path)
    if normalised is None:
        return _SAVED["os.path.realpath"](path, strict=strict)
    stripped = _strip_prefix(normalised)
    return "paker://" + stripped


def _paker_stat(path, *, dir_fd=None, follow_symlinks=True):
    normalised = _is_paker_path(path)
    if normalised is None:
        return _SAVED["os.stat"](
            path, dir_fd=dir_fd, follow_symlinks=follow_symlinks,
        )
    exists, is_dir = _path_exists(normalised)
    if not exists:
        raise FileNotFoundError(
            errno.ENOENT, "No such file or directory", _path_str(path),
        )
    return _make_stat(not is_dir)


# ---------------------------------------------------------------------------
# ctypes.CDLL interception
# ---------------------------------------------------------------------------


def _paker_cdll_init(self, name, mode=ctypes.DEFAULT_MODE, handle=None,
                     use_errno=False, use_last_error=False, winmode=None):
    if isinstance(name, str) and any(name.startswith(p) for p in _PAKER_PREFIXES):
        name = _materialize_native_lib(name)
    orig = _SAVED["ctypes.CDLL.__init__"]
    kwargs = dict(mode=mode, handle=handle,
                  use_errno=use_errno, use_last_error=use_last_error)
    if sys.platform == "win32" and winmode is not None:
        kwargs["winmode"] = winmode
    orig(self, name, **kwargs)


def _materialize_native_lib(paker_path: str) -> str:
    """Write a bundled native lib to a platform-appropriate ephemeral path.

    Returns a real filesystem path that ``ctypes.CDLL`` can dlopen. The
    materialized file stays alive until the shim is fully uninstalled.
    """
    if paker_path in _MATERIALIZED_LIBS:
        return _MATERIALIZED_LIBS[paker_path][0]

    data = _lookup_bytes(paker_path)
    if data is None:
        raise OSError(errno.ENOENT, "No such file or directory", paker_path)

    if sys.platform.startswith("linux"):
        from paker._loaders._linux import _get_memfd_create, MFD_CLOEXEC
        memfd_create = _get_memfd_create()
        fd = memfd_create(b"paker-ctypes", MFD_CLOEXEC)
        if fd < 0:
            raise OSError("memfd_create failed for ctypes lib")
        view = memoryview(data)
        while view:
            n = os.write(fd, view)
            view = view[n:]
        real_path = f"/proc/self/fd/{fd}"
        _MATERIALIZED_LIBS[paker_path] = (real_path, fd)
        return real_path

    # macOS / Windows / fallback: tempfile. On macOS we must keep the file
    # on disk long enough for codesign + dlopen; unlink happens in cleanup.
    fd, real_path = tempfile.mkstemp(prefix="paker-ct-")
    try:
        os.write(fd, data)
    except Exception:
        os.close(fd)
        os.unlink(real_path)
        raise
    os.close(fd)
    if sys.platform == "darwin":
        from paker._loaders._macos import _try_codesign
        _try_codesign(real_path)
    _MATERIALIZED_LIBS[paker_path] = (real_path, None)
    return real_path


def _cleanup_materialized_libs() -> None:
    for _, (path, fd) in _MATERIALIZED_LIBS.items():
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        elif os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
    _MATERIALIZED_LIBS.clear()
