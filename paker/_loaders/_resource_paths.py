"""Memory-backed file paths for ``importlib.resources.as_file``.

When library code does ``as_file(bundle_traversable)`` to get a filesystem
path for C-level ``fopen()`` (e.g. ``ssl.create_default_context(cafile=...)``),
the stdlib's default path is a tempfile copy. paker routes that through
here instead:

* **Linux** — ``memfd_create`` + ``/proc/self/fd/N``. The anonymous fd holds
  the bytes in kernel memory only; the returned path is valid for any
  in-process ``fopen`` call.

* **macOS** — ``mkstemp`` + write + ``unlink`` + ``/dev/fd/N``. The directory
  entry is removed immediately; the inode stays alive as long as the fd is
  open, and ``fopen("/dev/fd/N")`` finds it via the still-open vnode. Bytes
  transit the block cache but no persistent file exists.

* **Windows** — no user-mode memory-backed path primitive exists (ProjFS
  needs admin + OS feature, named pipes aren't seekable, API hooking trips
  EDR). The ``AUTO`` / ``TEMPFILE`` strategies fall back to a short-lived
  tempfile in ``%TEMP%`` with ``FILE_ATTRIBUTE_TEMPORARY`` — the cache
  manager keeps bytes in RAM where memory allows, but MFT + ``$LogFile``
  still record that a file existed. ``STRICT`` refuses on Windows.
"""

from __future__ import annotations

import contextlib
import enum
import os
import pathlib
import sys
import tempfile
from collections.abc import Iterator


class AsFileStrategy(str, enum.Enum):
    """Policy for how :func:`importlib.resources.as_file` serves paker resources.

    * ``STRICT`` — zero-disk or raise. Linux + macOS serve memory-backed fd
      paths; Windows raises :class:`RuntimeError` because no user-mode
      memory-backed path primitive is available.

    * ``AUTO`` — zero-disk where the platform allows; tempfile fallback on
      Windows. The Windows tempfile is opened with ``FILE_ATTRIBUTE_TEMPORARY``
      and deleted on context exit.

    * ``TEMPFILE`` — always a stdlib tempfile. Escape hatch for consumers
      that pass the path to a subprocess (``/proc/self/fd/N`` doesn't survive
      ``execve``) or otherwise need a real on-disk file.
    """

    STRICT = "strict"
    AUTO = "auto"
    TEMPFILE = "tempfile"


_DEFAULT = AsFileStrategy.STRICT


def resolve_strategy(
    strategy: AsFileStrategy | str | None,
) -> AsFileStrategy:
    """Normalise a caller-provided strategy.

    Only the embedding application can set the strategy — not the process
    environment. Unknown values raise :class:`ValueError`.
    """
    if strategy is None:
        return _DEFAULT
    if isinstance(strategy, AsFileStrategy):
        return strategy
    try:
        return AsFileStrategy(strategy.lower())
    except (AttributeError, ValueError) as e:
        valid = ", ".join(s.value for s in AsFileStrategy)
        raise ValueError(
            f"invalid as_file_strategy {strategy!r} (expected one of: {valid})"
        ) from e


@contextlib.contextmanager
def resource_path(
    data: bytes,
    *,
    suffix: str = "",
    strategy: AsFileStrategy = _DEFAULT,
) -> Iterator[pathlib.Path]:
    """Yield a ``pathlib.Path`` that ``fopen(str(path), ...)`` can read.

    The concrete backing depends on ``strategy`` and the running platform:

    ``STRICT`` → memfd (Linux) / mkstemp+unlink (macOS) / RuntimeError (Windows).
    ``AUTO`` → memfd / mkstemp+unlink / tempfile fallback on Windows.
    ``TEMPFILE`` → always a stdlib tempfile regardless of platform.

    The returned path is valid for the lifetime of the ``with`` block only.
    ``suffix`` is passed through so the filename ends in ``.pem`` etc. when
    that matters for the consumer (e.g. OpenSSL).
    """
    if strategy is AsFileStrategy.TEMPFILE:
        with _tempfile_path(data, suffix=suffix) as path:
            yield path
        return

    platform = sys.platform
    if platform.startswith("linux"):
        with _linux_memfd_path(data, suffix=suffix) as path:
            yield path
        return
    if platform == "darwin":
        with _darwin_unlink_path(data, suffix=suffix) as path:
            yield path
        return

    # Windows / other — no memory-backed primitive available.
    if strategy is AsFileStrategy.STRICT:
        raise RuntimeError(
            "paker: no memory-backed path primitive on this platform "
            f"({platform}). Use as_file_strategy='auto' for a tempfile "
            "fallback, or read .read_bytes() directly and pass cadata= "
            "instead of cafile=."
        )
    with _tempfile_path(data, suffix=suffix) as path:
        yield path


# ---------------------------------------------------------------------------
# Platform backends
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _linux_memfd_path(
    data: bytes, *, suffix: str,
) -> Iterator[pathlib.Path]:
    """``memfd_create`` + write + ``/proc/self/fd/N``. Zero disk."""
    import ctypes

    from paker._loaders._linux import MFD_CLOEXEC, _get_memfd_create

    memfd_create = _get_memfd_create()
    # ``name`` is a debugging aid only (shows up in ``/proc/self/fd``). The
    # suffix gives a meaningful label if forensics enumerate open fds.
    label = (b"paker-" + suffix.encode("ascii", "replace")).strip(b"-")
    fd = memfd_create(label or b"paker-resource", MFD_CLOEXEC)
    if fd < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"memfd_create failed: {os.strerror(errno)}")
    try:
        _write_all(fd, data)
        os.lseek(fd, 0, os.SEEK_SET)
        yield pathlib.Path(f"/proc/self/fd/{fd}")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


@contextlib.contextmanager
def _darwin_unlink_path(
    data: bytes, *, suffix: str,
) -> Iterator[pathlib.Path]:
    """``mkstemp`` + write + ``unlink`` + ``/dev/fd/N``. No persistent inode."""
    fd, path = tempfile.mkstemp(prefix="paker-", suffix=suffix)
    try:
        _write_all(fd, data)
        os.lseek(fd, 0, os.SEEK_SET)
        # Remove the directory entry immediately. The vnode survives as long
        # as the fd is open, so ``fopen("/dev/fd/N")`` still resolves.
        try:
            os.unlink(path)
        except OSError:
            pass
        yield pathlib.Path(f"/dev/fd/{fd}")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        # ``unlink`` already happened, but defend against a failure above.
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


@contextlib.contextmanager
def _tempfile_path(
    data: bytes, *, suffix: str,
) -> Iterator[pathlib.Path]:
    """Fallback: short-lived tempfile. Windows uses ``FILE_ATTRIBUTE_TEMPORARY``.

    Writes the bytes, closes the write handle, yields the path, unlinks on
    exit. On Windows, ``_set_temporary_attribute`` hints the cache manager
    to keep bytes in RAM where memory allows — still writes to MFT +
    ``$LogFile`` (forensically visible), but no explicit disk flush.
    """
    fd, path = tempfile.mkstemp(prefix="paker-", suffix=suffix)
    try:
        _write_all(fd, data)
    finally:
        os.close(fd)
    if sys.platform == "win32":
        _set_temporary_attribute(path)
    try:
        yield pathlib.Path(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _set_temporary_attribute(path: str) -> None:
    """Set ``FILE_ATTRIBUTE_TEMPORARY`` on a Windows tempfile.

    Hints the cache manager to defer flushing pages to disk; bytes stay in
    RAM where memory pressure permits. Fails silently if the Win32 call is
    unavailable (running under Wine, stripped-down systems).
    """
    try:
        import ctypes

        FILE_ATTRIBUTE_TEMPORARY = 0x100
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetFileAttributesW(
            ctypes.c_wchar_p(path), FILE_ATTRIBUTE_TEMPORARY,
        )
    except (OSError, AttributeError):
        pass


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]
