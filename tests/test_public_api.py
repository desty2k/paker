"""Tests for the public paker API (load, loads, dump, dumps)."""
import importlib.metadata
import io
import os
import sys
import json
import pytest

import paker
from paker._loaders._base import NativeLoaderPolicy
from paker._loaders._tempfile import TempFileLoader
from paker.importers import JsonImporter

TESTS_DATA = os.path.join(os.path.dirname(__file__), "tests_data")


class TestNamespacePackage:
    """Namespace packages (directories without __init__.py) carry no code.

    The bundle stores them with an empty ``code`` field; the loader skips
    ``exec`` and just provides the import hook. With encryption, the empty
    code must not be marked ``encrypted`` because there's no HMAC to verify.
    """

    def _pack(self, **kwargs):
        sys.path.insert(0, TESTS_DATA)
        try:
            return paker.dumps("testpackage", **kwargs)
        finally:
            sys.path.pop(0)
        for n in list(sys.modules):
            if n == "testpackage" or n.startswith("testpackage."):
                del sys.modules[n]

    def _clear(self):
        for n in list(sys.modules):
            if n == "testpackage" or n.startswith("testpackage."):
                del sys.modules[n]

    def test_namespace_node_not_encrypted(self):
        bundle = self._pack(key=b"\x11" * 32)
        nspkg_node = bundle["testpackage"]["modules"]["nspkg"]
        assert nspkg_node["type"] == "package"
        assert nspkg_node["code"] == ""
        assert nspkg_node.get("encrypted") is not True

    def test_namespace_loads_children(self):
        bundle = self._pack(key=b"\x11" * 32)
        self._clear()
        with paker.loads(bundle, key=b"\x11" * 32):
            from testpackage.nspkg import leaf
            assert leaf.VALUE == 42

    def test_namespace_loads_unencrypted(self):
        bundle = self._pack()
        self._clear()
        with paker.loads(bundle):
            from testpackage.nspkg import leaf
            assert leaf.VALUE == 42

    def test_nested_namespace_package_discovered(self, tmp_path):
        root = tmp_path / "root"
        package = root / "pkg"
        leaf_dir = package / "ns" / "inner"
        leaf_dir.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (leaf_dir / "leaf.py").write_text("VALUE = 7\n")

        sys.path.insert(0, str(root))
        try:
            bundle = paker.dumps("pkg")
        finally:
            sys.path.pop(0)
            for n in list(sys.modules):
                if n == "pkg" or n.startswith("pkg."):
                    del sys.modules[n]

        assert "ns" in bundle["pkg"]["modules"]
        assert "inner" in bundle["pkg"]["modules"]["ns"]["modules"]

        with paker.loads(bundle):
            from pkg.ns.inner import leaf
            assert leaf.VALUE == 7


class TestLoads:
    def test_from_dict(self, simple_module_dict):
        with paker.loads(simple_module_dict) as imp:
            assert isinstance(imp, JsonImporter)
            import greeter
            assert greeter.hello("paker") == "Hello, paker!"

    def test_from_str(self, simple_module_json):
        with paker.loads(simple_module_json) as imp:
            import greeter
            assert greeter.hello("str") == "Hello, str!"

    def test_from_bytes(self, simple_module_bytes):
        with paker.loads(simple_module_bytes) as imp:
            import greeter
            assert greeter.hello("bytes") == "Hello, bytes!"

    def test_from_bytearray(self, simple_module_bytes):
        ba = bytearray(simple_module_bytes)
        with paker.loads(ba) as imp:
            import greeter
            assert greeter.hello("bytearray") == "Hello, bytearray!"

    def test_invalid_type_raises(self):
        with pytest.raises(TypeError, match="must be dict, str, bytes or bytearray"):
            paker.loads(12345)

    def test_overwrite_false_does_not_replace(self, simple_module_dict):
        with paker.loads(simple_module_dict) as imp:
            import greeter
            updated = {
                "greeter": {
                    "type": "module", "extension": "py",
                    "code": "def hello(name):\n    return f'Goodbye, {name}!'\n",
                }
            }
            paker.loads(updated, overwrite=False)
            assert greeter.hello("test") == "Hello, test!"

    def test_overwrite_true_replaces(self, simple_module_dict):
        with paker.loads(simple_module_dict) as imp:
            updated = {
                "greeter": {
                    "type": "module", "extension": "py",
                    "code": "def hello(name):\n    return f'Goodbye, {name}!'\n",
                }
            }
            paker.loads(updated, overwrite=True)


class TestLoad:
    def test_load_from_file(self, simple_module_dict, tmp_path):
        p = tmp_path / "mod.json"
        p.write_text(json.dumps(simple_module_dict))
        with open(p, "r") as f:
            with paker.load(f) as imp:
                import greeter
                assert greeter.hello("file") == "Hello, file!"


class TestDumps:
    def test_dumps_testpackage(self):
        sys.path.insert(0, TESTS_DATA)
        try:
            result = paker.dumps("testpackage")
            assert "testpackage" in result
            pkg = result["testpackage"]
            assert pkg["type"] == "package"
            assert pkg["extension"] in ("py", "pyc")
            assert "core" in pkg["modules"]
            assert "subpackage" in pkg["modules"]
        finally:
            sys.path.pop(0)

    def test_dumps_returns_dict(self):
        sys.path.insert(0, TESTS_DATA)
        try:
            result = paker.dumps("testpackage")
            assert isinstance(result, dict)
        finally:
            sys.path.pop(0)

    def test_roundtrip(self):
        sys.path.insert(0, TESTS_DATA)
        try:
            dumped = paker.dumps("testpackage")
            serialized = json.dumps(dumped)

            for name in list(sys.modules):
                if name.startswith("testpackage"):
                    del sys.modules[name]

            with paker.loads(serialized) as imp:
                import testpackage
                assert testpackage.Fibonacci.fib(5) == [0, 1, 1, 2, 3]
                assert testpackage.Fibonacci.fib_odd(5) == [1, 1, 3]
        finally:
            sys.path.pop(0)


class TestDump:
    def test_dump_to_file(self, tmp_path):
        sys.path.insert(0, TESTS_DATA)
        try:
            out = tmp_path / "testpackage.json"
            with open(out, "w") as f:
                paker.dump("testpackage", f, indent=2)

            with open(out, "r") as f:
                data = json.load(f)
            assert "testpackage" in data
        finally:
            sys.path.pop(0)

    def test_dump_default_stores_source(self, tmp_path):
        """Default compile=False stores source for cross-version portability."""
        sys.path.insert(0, TESTS_DATA)
        try:
            out = tmp_path / "source.json"
            with open(out, "w") as f:
                paker.dump("testpackage", f)
            with open(out, "r") as f:
                data = json.load(f)
            assert data["testpackage"]["extension"] == "py"
        finally:
            sys.path.pop(0)

    def test_dump_default_preserves_source_text(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        source = "# important comment\nX    =    1\n"
        (root / "plain_source.py").write_text(source)

        sys.path.insert(0, str(root))
        try:
            data = paker.dumps("plain_source")
        finally:
            sys.path.pop(0)
            sys.modules.pop("plain_source", None)

        assert data["plain_source"]["code"] == source

    def test_dump_compile_true_stores_bytecode(self, tmp_path):
        """compile=True stores marshalled bytecode."""
        sys.path.insert(0, TESTS_DATA)
        try:
            out = tmp_path / "compiled.json"
            with open(out, "w") as f:
                paker.dump("testpackage", f, compile=True)
            with open(out, "r") as f:
                data = json.load(f)
            assert data["testpackage"]["extension"] == "pyc"
        finally:
            sys.path.pop(0)

    def test_dump_forwards_include_native_libs(self, monkeypatch):
        captured = {}

        def fake_dumps(*args, **kwargs):
            captured.update(kwargs)
            return {}

        monkeypatch.setattr(paker, "dumps", fake_dumps)

        paker.dump("pkg", io.StringIO(), include_native_libs=False)

        assert captured["include_native_libs"] is False


class TestPlatformDetection:
    def test_does_not_match_window_manager(self):
        from paker import _PLATFORM_RE
        assert not _PLATFORM_RE.search("window_manager")

    def test_does_not_match_winning_strategy(self):
        from paker import _PLATFORM_RE
        assert not _PLATFORM_RE.search("winning_strategy")

    def test_does_not_match_thin_win(self):
        from paker import _PLATFORM_RE
        assert not _PLATFORM_RE.search("thin_win")

    def test_matches_winconsole(self):
        from paker import _PLATFORM_RE
        assert _PLATFORM_RE.search("_winconsole")

    def test_matches_psutil_windows(self):
        from paker import _PLATFORM_RE
        assert _PLATFORM_RE.search("_psutil_windows")

    def test_matches_psutil_linux(self):
        from paker import _PLATFORM_RE
        assert _PLATFORM_RE.search("_psutil_linux")

    def test_matches_psbsd(self):
        from paker import _PLATFORM_RE
        assert _PLATFORM_RE.search("_psbsd")

    def test_skips_platform_message(self):
        from paker import _should_skip_import_error
        exc = ImportError("can only be imported on Windows")
        exc.name = None
        assert _should_skip_import_error(exc, "rich._win32_console")

    def test_skips_optional_feature_message(self):
        from paker import _should_skip_import_error
        exc = ImportError("The mcp package is required to use MCP helpers. Install it with: pip install anthropic[mcp].")
        exc.name = None
        assert _should_skip_import_error(exc, "anthropic.lib.tools.mcp")

    def test_skips_missing_optional_dep(self):
        from paker import _should_skip_import_error
        exc = ImportError("No module named 'hypothesis'")
        exc.name = "hypothesis"
        assert _should_skip_import_error(exc, "pandas._testing")

    def test_skips_missing_name_from_installed_container(self):
        """``from X import Y`` where Y is an optional dependency: X is
        installed, Y isn't anywhere on sys.path. We skip so the parent
        package still packs (seen on boto3's
        ``from botocore.compat import awscrt``).
        """
        from paker import _should_skip_import_error
        exc = ImportError(
            "cannot import name 'nonexistent_optional_dep' from 'json'"
        )
        exc.name = "json"
        assert _should_skip_import_error(exc, "json.decoder")

    def test_does_not_skip_when_target_itself_broken(self):
        """Direct import of X.Y where Y is a real-bug missing name —
        exc_name == target means the user asked for X.Y specifically and
        it failed. Propagate, don't skip.
        """
        from paker import _should_skip_import_error
        exc = ImportError("cannot import name 'nonexistent' from 'json'")
        exc.name = "json.decoder"
        assert not _should_skip_import_error(exc, "json.decoder")


def _pack(**kwargs):
    sys.path.insert(0, TESTS_DATA)
    try:
        return paker.dumps("testpackage", **kwargs)
    finally:
        sys.path.pop(0)


def _clear():
    for n in list(sys.modules):
        if n == "testpackage" or n.startswith("testpackage."):
            del sys.modules[n]


class TestMpInitializer:
    def test_returns_callable_and_tuple(self):
        bundle = _pack()
        fn, args = paker.mp_initializer(bundle)
        assert callable(fn)
        assert isinstance(args, tuple)

    def test_child_init_installs_importer(self):
        bundle = _pack()
        fn, args = paker.mp_initializer(bundle)
        _clear()
        fn(*args)
        import testpackage
        assert testpackage.Fibonacci.fib(6) == [0, 1, 1, 2, 3, 5]
        _clear()

    def test_with_key(self):
        key = b"\xab" * 32
        bundle = _pack(key=key)
        fn, args = paker.mp_initializer(bundle, key=key)
        _clear()
        fn(*args)
        import testpackage
        assert testpackage.Fibonacci.fib(5) == [0, 1, 1, 2, 3]
        _clear()


class TestFindDistributions:
    def test_bundled_dist_version_queryable(self):
        bundle = _pack()
        bundle["testpackage"]["dist_name"] = "testpackage"
        bundle["testpackage"]["dist_version"] = "1.2.3"
        _clear()
        importer = paker.loads(bundle)
        try:
            v = importlib.metadata.version("testpackage")
            assert v == "1.2.3"
        finally:
            importer.unload()
            _clear()

    def test_missing_dist_info_yields_nothing(self):
        bundle = _pack()
        _clear()
        importer = paker.loads(bundle)
        try:
            assert list(importer.find_distributions()) == []
        finally:
            importer.unload()
            _clear()

    def test_name_filter(self):
        bundle = _pack()
        bundle["testpackage"]["dist_name"] = "testpackage"
        bundle["testpackage"]["dist_version"] = "0.1.0"
        _clear()
        importer = paker.loads(bundle)
        try:
            ctx = importlib.metadata.DistributionFinder.Context(name="nonexistent")
            assert list(importer.find_distributions(ctx)) == []
            ctx2 = importlib.metadata.DistributionFinder.Context(name="testpackage")
            dists = list(importer.find_distributions(ctx2))
            assert len(dists) == 1
            assert dists[0].metadata["Version"] == "0.1.0"
        finally:
            importer.unload()
            _clear()


class TestNativeLoaderDisk:
    def test_disk_uses_tempfile_loader(self):
        bundle = _pack()
        _clear()
        importer = paker.loads(bundle, native_loader="disk")
        try:
            assert isinstance(importer._native_loader, TempFileLoader)
        finally:
            importer.unload()
            _clear()

    def test_disk_enum_passthrough(self):
        bundle = _pack()
        _clear()
        importer = paker.loads(bundle, native_loader=NativeLoaderPolicy.DISK)
        try:
            assert isinstance(importer._native_loader, TempFileLoader)
        finally:
            importer.unload()
            _clear()

    def test_auto_loads_module(self):
        bundle = _pack()
        _clear()
        importer = paker.loads(bundle, native_loader="auto")
        try:
            import testpackage
            assert testpackage.Fibonacci.fib(5) == [0, 1, 1, 2, 3]
        finally:
            importer.unload()
            _clear()


class TestCompileRoundtrip:
    def test_compile_false_stores_source(self):
        bundle = _pack(compile=False)
        assert bundle["testpackage"].get("extension") == "py"

    def test_compile_true_stores_bytecode(self):
        bundle = _pack(compile=True)
        assert bundle["testpackage"].get("extension") == "pyc"

    def test_compile_false_encrypted_roundtrip(self):
        key = b"\xcc" * 32
        bundle = _pack(key=key, compile=False)
        _clear()
        importer = paker.loads(bundle, key=key)
        try:
            import testpackage
            assert testpackage.Fibonacci.fib(6) == [0, 1, 1, 2, 3, 5]
        finally:
            importer.unload()
            _clear()

    def test_compile_true_encrypted_roundtrip(self):
        key = b"\xdd" * 32
        bundle = _pack(key=key, compile=True)
        _clear()
        importer = paker.loads(bundle, key=key)
        try:
            import testpackage
            assert testpackage.Fibonacci.fib(6) == [0, 1, 1, 2, 3, 5]
        finally:
            importer.unload()
            _clear()
