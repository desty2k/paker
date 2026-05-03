"""Tests for platform-specific native extension loaders."""
import os
import struct
import sys
import pytest

from paker._loaders._base import (
    get_native_loader, NativeLoader, NativeLoaderPolicy,
    _has_static_tls, _resolve_loader_policy,
)
from paker._loaders._tempfile import TempFileLoader


class TestGetNativeLoader:
    def test_returns_native_loader_instance(self):
        loader = get_native_loader()
        assert isinstance(loader, NativeLoader)

    def test_has_name(self):
        loader = get_native_loader()
        assert isinstance(loader.name, str)
        assert len(loader.name) > 0

    @pytest.mark.skipif(sys.platform != "linux", reason="Linux only")
    def test_linux_prefers_memfd(self):
        loader = get_native_loader()
        assert loader.name in ("memfd", "tempfile")

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
    def test_macos_uses_unlink(self):
        loader = get_native_loader()
        assert loader.name == "unlink"

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
    def test_windows_prefers_memimporter(self):
        loader = get_native_loader()
        assert loader.name in ("memimporter", "tempfile")


class TestTempFileLoader:
    def test_creates_tempdir_lazily(self):
        loader = TempFileLoader()
        assert loader._tempdir is None

    def test_tempdir_created_on_ensure(self):
        loader = TempFileLoader()
        path = loader._ensure_tempdir()
        assert os.path.isdir(path)
        loader.cleanup()

    def test_cleanup_removes_tempdir(self):
        loader = TempFileLoader()
        path = loader._ensure_tempdir()
        assert os.path.isdir(path)
        loader.cleanup()
        assert not os.path.isdir(path)

    def test_cleanup_idempotent(self):
        loader = TempFileLoader()
        loader._ensure_tempdir()
        loader.cleanup()
        loader.cleanup()

    def test_name(self):
        assert TempFileLoader().name == "tempfile"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux only")
class TestMemfdLoader:
    def test_import(self):
        from paker._loaders._linux import MemfdLoader
        loader = MemfdLoader()
        assert loader.name == "memfd"

    def test_cleanup_idempotent(self):
        from paker._loaders._linux import MemfdLoader
        loader = MemfdLoader()
        loader.cleanup()
        loader.cleanup()

    def test_extension_memfd_names_are_unique(self):
        from paker._loaders._linux import _memfd_name

        first = _memfd_name("pkg._first")
        second = _memfd_name("pkg._second")

        assert first != second
        assert b"pkg__first" in first
        assert b"pkg__second" in second
        assert len(first) <= 249

    def test_extension_memfd_name_sanitizes_path_separators(self):
        from paker._loaders._linux import _memfd_name

        name = _memfd_name("pkg.bad/name")

        assert b"/" not in name
        assert b"paker" not in name


def _build_pe(*, magic: int, tls_rva: int = 0, tls_size: int = 0) -> bytes:
    """Build a minimal synthetic PE with controllable TLS directory entry."""
    dos_header = bytearray(64)
    dos_header[0:2] = b"MZ"
    pe_off = 64
    struct.pack_into("<I", dos_header, 0x3C, pe_off)

    signature = b"PE\x00\x00"
    coff_header = bytearray(20)

    if magic == 0x20B:  # PE32+
        opt_header = bytearray(240)
        struct.pack_into("<H", opt_header, 0, 0x20B)
        # TLS is data directory index 9, each entry is 8 bytes
        # PE32+ data directories start at offset 112
        tls_off = 112 + 9 * 8  # = 184
        struct.pack_into("<II", opt_header, tls_off, tls_rva, tls_size)
    elif magic == 0x10B:  # PE32
        opt_header = bytearray(224)
        struct.pack_into("<H", opt_header, 0, 0x10B)
        # PE32 data directories start at offset 96
        tls_off = 96 + 9 * 8  # = 168
        struct.pack_into("<II", opt_header, tls_off, tls_rva, tls_size)
    else:
        opt_header = bytearray(16)
        struct.pack_into("<H", opt_header, 0, magic)

    return bytes(dos_header) + signature + bytes(coff_header) + bytes(opt_header)


class TestHasStaticTls:
    def test_pe32plus_with_tls(self):
        pe = _build_pe(magic=0x20B, tls_rva=0x1000, tls_size=40)
        assert _has_static_tls(pe) is True

    def test_pe32plus_without_tls(self):
        pe = _build_pe(magic=0x20B, tls_rva=0, tls_size=0)
        assert _has_static_tls(pe) is False

    def test_pe32_with_tls(self):
        pe = _build_pe(magic=0x10B, tls_rva=0x2000, tls_size=24)
        assert _has_static_tls(pe) is True

    def test_pe32_without_tls(self):
        pe = _build_pe(magic=0x10B, tls_rva=0, tls_size=0)
        assert _has_static_tls(pe) is False

    def test_unknown_magic_returns_false(self):
        pe = _build_pe(magic=0x9999)
        assert _has_static_tls(pe) is False

    def test_not_mz_returns_false(self):
        assert _has_static_tls(b"\x00" * 256) is False

    def test_too_short_returns_false(self):
        assert _has_static_tls(b"MZ") is False
        assert _has_static_tls(b"") is False

    def test_truncated_pe_header_returns_false(self):
        pe = _build_pe(magic=0x20B, tls_rva=0x1000, tls_size=40)
        assert _has_static_tls(pe[:80]) is False

    def test_tls_rva_nonzero_but_size_zero(self):
        pe = _build_pe(magic=0x20B, tls_rva=0x1000, tls_size=0)
        assert _has_static_tls(pe) is False


class TestNativeLoaderPolicy:
    def test_auto_returns_platform_loader(self):
        loader = get_native_loader("auto")
        assert isinstance(loader, NativeLoader)
        assert loader.name != "tempfile" or sys.platform not in ("darwin", "linux")

    def test_disk_returns_tempfile_loader(self):
        loader = get_native_loader("disk")
        assert loader.name == "tempfile"

    def test_enum_passthrough(self):
        loader = get_native_loader(NativeLoaderPolicy.DISK)
        assert loader.name == "tempfile"

    def test_none_defaults_to_auto(self):
        loader = get_native_loader(None)
        assert isinstance(loader, NativeLoader)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="invalid native_loader policy"):
            _resolve_loader_policy("invalid")

    def test_case_insensitive(self):
        loader = get_native_loader("DISK")
        assert loader.name == "tempfile"
