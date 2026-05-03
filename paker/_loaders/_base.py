"""Native extension loaders — strategy pattern.

A `NativeLoader` is responsible for getting a `.so`/`.pyd`/`.dll` loaded into
the interpreter without going through the filesystem where possible.

The plaintext bytes pass through this layer, so the interface is deliberately
narrow: the caller (`JsonImporter.exec_module`) decrypts in C and passes the
plaintext exactly once. For the Linux/macOS fd-based loaders we expose a
`load_from_fd` path so `_paker_crypto.decrypt_to_fd` can write plaintext
directly into a memfd/tempfile, avoiding a Python-visible copy entirely.
The Windows PE loader (`_memimporter`) requires plaintext bytes as input;
we feed it via a caller-scrubbed bytearray, which is the best achievable
until `_memimporter` gains an encrypted-input entry point.
"""

import abc
import enum
import importlib.machinery
import importlib.util
import logging
import struct
import sys
from typing import Callable


class NativeLoaderPolicy(enum.Enum):
    """Controls how native extensions (.so/.pyd/.dll) are loaded at runtime.

    AUTO — platform-specific best strategy (memfd, MemoryModule, dlopen+unlink).
    DISK — always use the OS loader (LoadLibrary / dlopen) via a temp file.
    """
    AUTO = "auto"
    DISK = "disk"

logger = logging.getLogger(__name__)

DataResolver = Callable[[str], bytes | None]


class NativeLoader(abc.ABC):
    """Base class for platform-specific native extension loaders (.so, .pyd, .dll).

    Two entry points:
    - `load`: caller hands over plaintext bytes. Used for the Windows in-memory
      PE loader and the tempfile fallback. Implementations are expected to
      consume the data quickly and not retain references.
    - `load_from_fd`: caller delegates plaintext production to the loader —
      the loader owns the file descriptor, asks the caller's `write_fn` to
      fill it, then loads via `dlopen("/proc/self/fd/N")` on Linux or
      `write + dlopen + unlink` on macOS. Plaintext never enters Python as a
      `bytes` or `bytearray` object.

    Windows and the tempfile fallback implement only `load`. Linux and macOS
    implement both.
    """

    supports_fd: bool = False

    @abc.abstractmethod
    def load(
        self,
        fullname: str,
        data: bytes,
        initfuncname: str,
        spec,
        resolve_dependency: DataResolver | None = None,
        bundled_libs: list | None = None,
    ) -> "types.ModuleType":
        """Load a native extension module from plaintext bytes.

        ``bundled_libs`` is an optional list of ``(layout_path, raw_bytes)``
        for wheel-bundled native dependencies (e.g. macOS ``.dylibs/``,
        Linux ``<pkg>.libs/``). Loaders that need the sibling filesystem
        layout (macOS, Linux tempfile fallback, Windows) recreate it
        before calling into the OS loader. Linux's ``memfd`` loader
        pre-dlopen's them with ``RTLD_GLOBAL`` instead.
        """

    def load_from_fd(
        self,
        fullname: str,
        write_fn: Callable[[int], None],
        byte_count: int,
        initfuncname: str,
        spec,
        resolve_dependency: DataResolver | None = None,
        bundled_libs: list | None = None,
    ) -> "types.ModuleType":
        """Load a native extension by first opening an fd and letting the caller
        fill it. Default implementation raises — override on platforms that
        can produce an fd without materializing plaintext in Python memory.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support fd-based loading"
        )

    @abc.abstractmethod
    def cleanup(self) -> None:
        """Release any resources held by this loader (temp files, etc.)."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable name for this loader strategy."""


def _load_dynamic(name: str, path: str):
    """Load a native extension from a file path using importlib.

    Uses an explicit ExtensionFileLoader so the file extension doesn't matter.
    """
    loader = importlib.machinery.ExtensionFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    if spec is None:
        raise ImportError(f"cannot create spec for {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return sys.modules[name]


def _has_static_tls(data: bytes) -> bool:
    """Check if a PE file uses Static TLS (IMAGE_DIRECTORY_ENTRY_TLS)."""
    if len(data) < 64 or data[:2] != b"MZ":
        return False
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    if pe_off + 24 > len(data):
        return False
    opt_start = pe_off + 4 + 20
    magic = struct.unpack_from("<H", data, opt_start)[0]
    if magic == 0x20B:  # PE32+
        tls_dir_offset = opt_start + 184
    elif magic == 0x10B:  # PE32
        tls_dir_offset = opt_start + 168
    else:
        return False
    if tls_dir_offset + 8 > len(data):
        return False
    tls_rva, tls_size = struct.unpack_from("<II", data, tls_dir_offset)
    return tls_size > 0


def _resolve_loader_policy(
    policy: NativeLoaderPolicy | str | None,
) -> NativeLoaderPolicy:
    if policy is None:
        return NativeLoaderPolicy.AUTO
    if isinstance(policy, NativeLoaderPolicy):
        return policy
    try:
        return NativeLoaderPolicy(policy.lower())
    except (ValueError, AttributeError):
        raise ValueError(
            f"invalid native_loader policy {policy!r}, "
            f"expected {[e.value for e in NativeLoaderPolicy]}"
        )


def get_native_loader(
    policy: NativeLoaderPolicy | str | None = None,
) -> NativeLoader:
    """Return the best available NativeLoader for the current platform."""
    resolved = _resolve_loader_policy(policy)
    if resolved == NativeLoaderPolicy.DISK:
        from paker._loaders._tempfile import TempFileLoader
        return TempFileLoader()

    if sys.platform == "win32":
        try:
            from paker._loaders._windows import MemImporterLoader
            return MemImporterLoader()
        except ImportError:
            logger.debug("_memimporter not available, falling back to temp file loader")

    if sys.platform == "linux":
        try:
            from paker._loaders._linux import MemfdLoader
            return MemfdLoader()
        except (ImportError, OSError):
            logger.debug("memfd_create not available, falling back to temp file loader")

    if sys.platform == "darwin":
        try:
            from paker._loaders._macos import UnlinkLoader
            return UnlinkLoader()
        except (ImportError, OSError):
            logger.debug("UnlinkLoader not available, falling back to temp file loader")

    from paker._loaders._tempfile import TempFileLoader
    return TempFileLoader()
