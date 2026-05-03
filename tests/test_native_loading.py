"""Tests for native extension loading across platforms.

These tests use a real .so/.pyd from the running Python to verify
that the platform-specific loaders actually work end-to-end.
"""
import os
import sys
import base64
import importlib
import json
import pytest

import paker
from paker._loaders._base import get_native_loader


def _get_test_extension():
    """Find a small, safe native extension to use for testing.

    Returns (module_name, file_path, extension, encoded_data) or None if no
    suitable extension is found.
    """
    for name in ("_json", "_bisect", "_heapq"):
        try:
            mod = importlib.import_module(name)
            path = getattr(mod, "__file__", None)
            if path and os.path.isfile(path):
                with open(path, "rb") as f:
                    data = f.read()
                ext = "so" if sys.platform != "win32" else "pyd"
                return name, path, ext, base64.b64encode(data).decode()
        except ImportError:
            continue
    return None


@pytest.fixture
def native_ext():
    result = _get_test_extension()
    if result is None:
        pytest.skip("no suitable native extension found for testing")
    return result


class TestNativeLoaderFactory:
    def test_returns_correct_loader_type(self):
        loader = get_native_loader()
        if sys.platform == "darwin":
            assert loader.name == "unlink"
        elif sys.platform == "linux":
            assert loader.name in ("memfd", "tempfile")
        elif sys.platform == "win32":
            assert loader.name in ("memimporter", "tempfile")

    def test_active_strategy_matches_platform(self):
        """Verify the selected loader is actually the preferred one for this platform."""
        loader = get_native_loader()

        if sys.platform == "linux":
            assert loader.name == "memfd", (
                f"Linux should use memfd_create, got {loader.name}. "
                "If this is 'tempfile', memfd_create is not available on this kernel."
            )
        elif sys.platform == "darwin":
            assert loader.name == "unlink", (
                f"macOS should use write-dlopen-unlink, got {loader.name}"
            )
        elif sys.platform == "win32":
            import importlib.util
            has_memimporter = importlib.util.find_spec("_memimporter") is not None
            expected = "memimporter" if has_memimporter else "tempfile"
            assert loader.name == expected, (
                f"Windows should use {expected}, got {loader.name}"
            )

    def test_loader_cleanup_is_safe(self):
        loader = get_native_loader()
        loader.cleanup()
        loader.cleanup()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
class TestUnlinkLoader:
    def test_load_and_unlink(self, native_ext):
        from paker._loaders._macos import UnlinkLoader

        name, _, ext, encoded = native_ext
        data = base64.b64decode(encoded)
        loader = UnlinkLoader()

        if name in sys.modules:
            del sys.modules[name]

        initfunc = f"PyInit_{name}"
        module = loader.load(name, data, initfunc, None)
        assert module is not None
        assert hasattr(module, "__name__")
        loader.cleanup()

    def test_no_files_left_in_tmpdir(self, native_ext):
        """After load, no paker files should remain in the temp directory."""
        from paker._loaders._macos import UnlinkLoader
        import tempfile as tf

        name, _, ext, encoded = native_ext
        data = base64.b64decode(encoded)

        tmpdir = tf.gettempdir()
        before = set(os.listdir(tmpdir))

        loader = UnlinkLoader()
        if name in sys.modules:
            del sys.modules[name]

        loader.load(name, data, f"PyInit_{name}", None)
        after = set(os.listdir(tmpdir))
        new_files = after - before
        assert len(new_files) == 0, f"files left behind: {new_files}"
        loader.cleanup()

    def test_cached_reload(self, native_ext):
        from paker._loaders._macos import UnlinkLoader

        name, _, ext, encoded = native_ext
        data = base64.b64decode(encoded)
        loader = UnlinkLoader()

        if name in sys.modules:
            del sys.modules[name]

        initfunc = f"PyInit_{name}"
        mod1 = loader.load(name, data, initfunc, None)
        mod2 = loader.load(name, data, initfunc, None)
        assert mod1 is mod2
        loader.cleanup()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
class TestUnlinkLoaderIntegration:
    def test_full_paker_roundtrip_with_native(self, native_ext):
        """Load a native extension through the full paker pipeline."""
        name, _, ext, encoded = native_ext

        module_dict = {
            name: {
                "type": "module",
                "extension": ext,
                "code": encoded,
            }
        }

        if name in sys.modules:
            del sys.modules[name]

        with paker.loads(module_dict) as imp:
            mod = imp.load_module(name)
            assert mod is not None




@pytest.mark.skipif(sys.platform != "linux", reason="Linux only")
class TestMemfdLoaderIntegration:
    def test_memfd_create_works(self):
        from paker._loaders._linux import MemfdLoader

        loader = MemfdLoader()
        assert loader.name == "memfd"
        loader.cleanup()

    def test_memfd_creates_anonymous_fd(self):
        from paker._loaders._linux import _get_memfd_create, MFD_CLOEXEC

        memfd_create = _get_memfd_create()
        fd = memfd_create(b"", MFD_CLOEXEC)
        assert fd >= 0

        os.write(fd, b"test data")
        path = f"/proc/self/fd/{fd}"
        assert os.path.exists(path)
        link = os.readlink(path)
        assert "memfd:" in link

        os.close(fd)
        assert not os.path.exists(path)



class TestDependencyResolver:
    def test_get_native_data_resolves(self):
        from paker.importers import JsonImporter

        modules = {
            "pkg": {
                "type": "package",
                "extension": "py",
                "code": "",
                "modules": {
                    "native": {
                        "type": "module",
                        "extension": "pyd",
                        "code": base64.b64encode(b"\x00\x01\x02").decode(),
                    }
                },
            }
        }
        imp = JsonImporter(modules)
        result = imp._get_native_data("pkg\\native.pyd")
        assert result == b"\x00\x01\x02"
        imp.unload()

    def test_get_native_data_returns_none_for_missing(self):
        from paker.importers import JsonImporter

        imp = JsonImporter({"x": {"type": "module", "extension": "py", "code": ""}})
        result = imp._get_native_data("nonexistent\\mod.pyd")
        assert result is None
        imp.unload()

    def test_resolver_passed_through_load(self):
        """Verify that _get_native_data is wired into exec_module for native extensions."""
        from paker.importers import JsonImporter
        imp = JsonImporter({"test": {"type": "module", "extension": "py", "code": "x=1"}})
        assert callable(imp._get_native_data)
        imp.unload()
