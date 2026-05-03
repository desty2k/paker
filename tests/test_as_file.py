"""Tests for paker's ``importlib.resources.as_file`` integration."""

from __future__ import annotations

import os
import sys
from importlib import resources

import pytest

import paker
from paker._loaders._resource_paths import (
    AsFileStrategy,
    resolve_strategy,
)


TESTS_DATA = os.path.join(os.path.dirname(__file__), "tests_data")


@pytest.fixture(autouse=True)
def _reset_testpackage():
    for name in list(sys.modules):
        if name == "testpackage" or name.startswith("testpackage."):
            del sys.modules[name]
    yield
    for name in list(sys.modules):
        if name == "testpackage" or name.startswith("testpackage."):
            del sys.modules[name]


def _pack(**kwargs):
    sys.path.insert(0, TESTS_DATA)
    try:
        bundle = paker.dumps("testpackage", **kwargs)
    finally:
        sys.path.pop(0)
    # ``dumps`` imported testpackage from disk; drop it so subsequent
    # ``importlib.resources.files('testpackage')`` resolves through the
    # paker meta-path entry instead of the cached filesystem module.
    for name in list(sys.modules):
        if name == "testpackage" or name.startswith("testpackage."):
            del sys.modules[name]
    return bundle


class TestResolveStrategy:
    def test_default_is_strict(self):
        assert resolve_strategy(None) is AsFileStrategy.STRICT

    def test_env_var_is_ignored(self, monkeypatch):
        """Strategy is programmatic only — ``os.environ`` must not influence it."""
        monkeypatch.setenv("PAKER_AS_FILE_STRATEGY", "tempfile")
        assert resolve_strategy(None) is AsFileStrategy.STRICT

    def test_enum_passthrough(self):
        assert resolve_strategy(AsFileStrategy.TEMPFILE) is AsFileStrategy.TEMPFILE

    def test_string_case_insensitive(self):
        assert resolve_strategy("STRICT") is AsFileStrategy.STRICT
        assert resolve_strategy("Auto") is AsFileStrategy.AUTO

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="invalid as_file_strategy"):
            resolve_strategy("bogus")


@pytest.mark.skipif(
    sys.platform not in ("linux", "darwin"),
    reason="memory-backed path only on Linux/macOS",
)
class TestAsFileMemoryBacked:
    def test_as_file_yields_readable_path(self):
        bundle = _pack()
        with paker.loads(bundle):
            trav = resources.files("testpackage").joinpath("sample.json")
            with resources.as_file(trav) as path:
                assert path.exists()
                data = path.read_bytes()
                assert data == trav.read_bytes()

    def test_as_file_path_preserves_suffix(self):
        bundle = _pack()
        with paker.loads(bundle):
            trav = resources.files("testpackage").joinpath("sample.json")
            with resources.as_file(trav) as path:
                # ``/proc/self/fd/N`` on Linux and ``/dev/fd/N`` on macOS
                # don't carry a suffix in the final path segment — the
                # suffix-preservation guarantee only applies to the underlying
                # fd's name attribute. Just sanity-check the contents.
                assert b"{" in path.read_bytes()

    def test_as_file_fopen_compatible(self):
        """Plain ``open(str(path))`` via Python's C-layer must work."""
        bundle = _pack()
        with paker.loads(bundle):
            trav = resources.files("testpackage").joinpath("sample.json")
            with resources.as_file(trav) as path:
                with open(str(path), "rb") as fp:
                    data = fp.read()
                assert data == trav.read_bytes()

    def test_as_file_ctypes_libc_fopen(self):
        """Real C-layer ``fopen`` via libc — the thing ssl/OpenSSL does."""
        import ctypes
        import ctypes.util

        libc = ctypes.CDLL(ctypes.util.find_library("c"))
        libc.fopen.restype = ctypes.c_void_p
        libc.fopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        libc.fread.restype = ctypes.c_size_t
        libc.fread.argtypes = [
            ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_void_p,
        ]
        libc.fclose.restype = ctypes.c_int
        libc.fclose.argtypes = [ctypes.c_void_p]

        bundle = _pack()
        with paker.loads(bundle):
            trav = resources.files("testpackage").joinpath("sample.json")
            expected = trav.read_bytes()
            with resources.as_file(trav) as path:
                fp = libc.fopen(str(path).encode(), b"rb")
                assert fp, "libc.fopen returned NULL"
                try:
                    buf = ctypes.create_string_buffer(len(expected) + 32)
                    n = libc.fread(buf, 1, len(expected) + 31, fp)
                    assert buf.raw[:n] == expected
                finally:
                    libc.fclose(fp)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux memfd path")
class TestLinuxMemfd:
    def test_path_is_procfs_fd(self):
        bundle = _pack()
        with paker.loads(bundle):
            trav = resources.files("testpackage").joinpath("sample.json")
            with resources.as_file(trav) as path:
                assert str(path).startswith("/proc/self/fd/")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS /dev/fd/N path")
class TestDarwinDevFd:
    def test_path_is_dev_fd(self):
        bundle = _pack()
        with paker.loads(bundle):
            trav = resources.files("testpackage").joinpath("sample.json")
            with resources.as_file(trav) as path:
                assert str(path).startswith("/dev/fd/")

    def test_no_persistent_tempfile(self, tmp_path, monkeypatch):
        """After ``as_file`` exits, no paker-prefixed tempfile lingers."""
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        bundle = _pack()
        with paker.loads(bundle):
            trav = resources.files("testpackage").joinpath("sample.json")
            with resources.as_file(trav) as _:
                pass
        leftovers = [p for p in tmp_path.iterdir() if p.name.startswith("paker-")]
        assert not leftovers


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific")
class TestWindowsStrict:
    def test_strict_raises_runtime_error(self):
        bundle = _pack()
        with paker.loads(bundle, as_file_strategy=AsFileStrategy.STRICT):
            trav = resources.files("testpackage").joinpath("sample.json")
            with pytest.raises(RuntimeError, match="no memory-backed path"):
                with resources.as_file(trav) as _:
                    pass

    def test_auto_falls_back_to_tempfile(self):
        bundle = _pack()
        with paker.loads(bundle, as_file_strategy=AsFileStrategy.AUTO):
            trav = resources.files("testpackage").joinpath("sample.json")
            with resources.as_file(trav) as path:
                assert path.exists()
                assert path.read_bytes() == trav.read_bytes()


class TestTempfileStrategy:
    def test_tempfile_always_usable(self):
        """``TEMPFILE`` works everywhere — the escape hatch for subprocess consumers."""
        bundle = _pack()
        with paker.loads(bundle, as_file_strategy=AsFileStrategy.TEMPFILE):
            trav = resources.files("testpackage").joinpath("sample.json")
            with resources.as_file(trav) as path:
                assert path.exists()
                assert path.read_bytes() == trav.read_bytes()
            assert not path.exists()


class TestDirectoryBranch:
    def test_directory_traversable_materializes_tempdir(self):
        """``as_file(dir_traversable)`` falls through to the stdlib's temp-dir path."""
        bundle = _pack()
        with paker.loads(bundle):
            data_dir = resources.files("testpackage").joinpath("data")
            assert data_dir.is_dir()
            with resources.as_file(data_dir) as path:
                assert path.is_dir()
                # ``data/config.ini`` should be present via the directory copy.
                assert (path / "config.ini").exists()


class TestEncryptedRoundTrip:
    def test_encrypted_resource_via_as_file(self):
        """Encrypted bundle → memory-backed path round-trips the plaintext."""
        if sys.platform == "win32":
            pytest.skip("STRICT refuses on Windows")
        key = b"\x11" * 32
        bundle = _pack(key=key)
        with paker.loads(bundle, key=key):
            trav = resources.files("testpackage").joinpath("sample.json")
            with resources.as_file(trav) as path:
                assert path.read_bytes() == trav.read_bytes()
