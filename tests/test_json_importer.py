"""Tests for JsonImporter — the PEP 451 meta path finder/loader."""
import sys
import json
import importlib
import pytest

import paker
from paker.importers import JsonImporter
from paker.exceptions import PakerImportError


class TestJsonImporterInit:
    def test_registers_on_meta_path(self, simple_module_dict):
        imp = JsonImporter(simple_module_dict)
        assert imp in sys.meta_path
        imp.unload()

    def test_unload_removes_from_meta_path(self, simple_module_dict):
        imp = JsonImporter(simple_module_dict)
        imp.unload()
        assert imp not in sys.meta_path


class TestFindSpec:
    def test_finds_existing_module(self, simple_module_dict):
        imp = JsonImporter(simple_module_dict)
        spec = imp.find_spec("greeter", None)
        assert spec is not None
        assert spec.name == "greeter"
        assert spec.loader is imp
        imp.unload()

    def test_returns_none_for_missing(self, simple_module_dict):
        imp = JsonImporter(simple_module_dict)
        assert imp.find_spec("nonexistent", None) is None
        imp.unload()

    def test_finds_package(self, simple_package_dict):
        imp = JsonImporter(simple_package_dict)
        spec = imp.find_spec("mathpkg", None)
        assert spec is not None
        assert spec.submodule_search_locations is not None
        imp.unload()

    def test_finds_submodule(self, simple_package_dict):
        imp = JsonImporter(simple_package_dict)
        spec = imp.find_spec("mathpkg.add", None)
        assert spec is not None
        assert spec.name == "mathpkg.add"
        assert spec.submodule_search_locations is None
        imp.unload()

    def test_finds_nested_package(self, nested_package_dict):
        imp = JsonImporter(nested_package_dict)
        spec = imp.find_spec("outer.inner", None)
        assert spec is not None
        assert spec.submodule_search_locations is not None
        imp.unload()

    def test_finds_deeply_nested_module(self, nested_package_dict):
        imp = JsonImporter(nested_package_dict)
        spec = imp.find_spec("outer.inner.leaf", None)
        assert spec is not None
        imp.unload()


class TestExecModule:
    def test_imports_simple_module(self, simple_module_dict):
        with JsonImporter(simple_module_dict) as imp:
            import greeter
            assert greeter.hello("World") == "Hello, World!"

    def test_module_has_spec(self, simple_module_dict):
        with JsonImporter(simple_module_dict) as imp:
            import greeter
            assert greeter.__spec__ is not None
            assert greeter.__spec__.name == "greeter"

    def test_module_has_loader(self, simple_module_dict):
        with JsonImporter(simple_module_dict) as imp:
            import greeter
            assert greeter.__loader__ is imp

    def test_imports_package_with_submodules(self, simple_package_dict):
        with JsonImporter(simple_package_dict) as imp:
            import mathpkg
            assert mathpkg.add(2, 3) == 5
            assert mathpkg.mul(4, 5) == 20

    def test_imports_submodule_directly(self, simple_package_dict):
        with JsonImporter(simple_package_dict) as imp:
            from mathpkg.add import add
            assert add(10, 20) == 30

    def test_nested_package_import(self, nested_package_dict):
        with JsonImporter(nested_package_dict) as imp:
            from outer.inner import VALUE
            assert VALUE == 42

    def test_deeply_nested_import(self, nested_package_dict):
        with JsonImporter(nested_package_dict) as imp:
            from outer.inner.leaf import VALUE
            assert VALUE == 42

    def test_module_in_sys_modules(self, simple_module_dict):
        with JsonImporter(simple_module_dict) as imp:
            import greeter
            assert "greeter" in sys.modules
            assert sys.modules["greeter"] is greeter

    def test_package_path_set(self, simple_package_dict):
        with JsonImporter(simple_package_dict) as imp:
            import mathpkg
            assert hasattr(mathpkg, "__path__")
            assert len(mathpkg.__path__) > 0


class TestContextManager:
    def test_single_context_unloads_all(self, simple_module_dict):
        with JsonImporter(simple_module_dict) as imp:
            import greeter
            assert "greeter" in sys.modules
        assert "greeter" not in sys.modules
        assert imp not in sys.meta_path

    def test_nested_context_unloads_last_added(self):
        base = {
            "pow_mod": {
                "type": "module", "extension": "py",
                "code": "pow_fn = lambda x, y: x ** y\n",
            }
        }
        extra = {
            "sqr_mod": {
                "type": "module", "extension": "py",
                "code": "from pow_mod import pow_fn\nsqr = lambda x: pow_fn(x, 2)\n",
            }
        }

        with paker.loads(base) as loader:
            import pow_mod
            assert pow_mod.pow_fn(2, 3) == 8

            with paker.loads(extra) as loader2:
                import sqr_mod
                assert sqr_mod.sqr(5) == 25

            assert "sqr_mod" not in sys.modules
            assert "pow_mod" in sys.modules

        assert "pow_mod" not in sys.modules


class TestAddModule:
    def test_add_and_import(self, simple_module_dict):
        imp = JsonImporter(simple_module_dict)
        new_mod = {
            "type": "module", "extension": "py",
            "code": "X = 99\n",
        }
        imp.add_module("extra", new_mod)
        import extra
        assert extra.X == 99
        imp.unload()

    def test_add_non_dict_raises(self, simple_module_dict):
        imp = JsonImporter(simple_module_dict)
        with pytest.raises(PakerImportError):
            imp.add_module("bad", "not a dict")
        imp.unload()


class TestUnloadModule:
    def test_unload_single(self, simple_module_dict):
        imp = JsonImporter(simple_module_dict)
        import greeter
        assert "greeter" in sys.modules
        imp.unload_module("greeter")
        assert "greeter" not in sys.modules
        imp.unload()

    def test_unload_by_module_object(self, simple_module_dict):
        imp = JsonImporter(simple_module_dict)
        import greeter
        imp.unload_module(greeter)
        assert "greeter" not in sys.modules
        imp.unload()


class TestErrorHandling:
    def test_bad_extension_raises(self):
        bad = {
            "badmod": {
                "type": "module", "extension": "xyz",
                "code": "hello = 1\n",
            }
        }
        imp = JsonImporter(bad)
        with pytest.raises(PakerImportError, match="unsupported extension"):
            import badmod
        imp.unload()

    def test_syntax_error_propagates(self):
        bad = {
            "syntaxmod": {
                "type": "module", "extension": "py",
                "code": "def broken(\n",
            }
        }
        imp = JsonImporter(bad)
        with pytest.raises(SyntaxError):
            import syntaxmod
        imp.unload()

    def test_import_error_in_code_propagates(self):
        bad = {
            "importmod": {
                "type": "module", "extension": "py",
                "code": "import this_does_not_exist_anywhere_12345\n",
            }
        }
        imp = JsonImporter(bad)
        with pytest.raises(ModuleNotFoundError):
            import importmod
        imp.unload()


class TestLegacyProtocol:
    """Test backward compat with find_module/load_module for tools that use them."""

    def test_find_module_returns_self(self, simple_module_dict):
        imp = JsonImporter(simple_module_dict)
        assert imp.find_module("greeter") is imp
        imp.unload()

    def test_find_module_returns_none_for_missing(self, simple_module_dict):
        imp = JsonImporter(simple_module_dict)
        assert imp.find_module("nonexistent") is None
        imp.unload()

    def test_load_module(self, simple_module_dict):
        imp = JsonImporter(simple_module_dict)
        mod = imp.load_module("greeter")
        assert mod.hello("test") == "Hello, test!"
        imp.unload()


class TestKeyHygiene:
    """The key buffer must be scrubbed and dropped on unload."""

    def test_key_is_internal_bytearray(self, simple_module_dict):
        import os
        key = os.urandom(32)
        imp = JsonImporter(simple_module_dict, key=key)
        try:
            assert isinstance(imp._key, bytearray)
            assert imp._key is not key
            assert bytes(imp._key) == key
        finally:
            imp.unload()

    def test_key_scrubbed_on_unload(self, simple_module_dict):
        import os
        key = os.urandom(32)
        imp = JsonImporter(simple_module_dict, key=key)
        internal = imp._key
        imp.unload()
        assert imp._key is None
        assert bytes(internal) == b"\x00" * 32

    def test_caller_key_untouched(self, simple_module_dict):
        import os
        key = bytearray(os.urandom(32))
        original = bytes(key)
        imp = JsonImporter(simple_module_dict, key=bytes(key))
        imp.unload()
        assert bytes(key) == original


class TestBundleDictIsolation:
    def test_unload_does_not_clear_caller_dict(self, simple_module_dict):
        original_keys = set(simple_module_dict.keys())
        imp = JsonImporter(simple_module_dict)
        imp.unload()
        assert set(simple_module_dict.keys()) == original_keys

    def test_bundle_reusable_after_unload(self, simple_module_dict):
        imp1 = JsonImporter(simple_module_dict)
        imp1.unload()
        for n in list(sys.modules):
            if n.startswith("_paker_test"):
                del sys.modules[n]
        imp2 = JsonImporter(simple_module_dict)
        spec = imp2.find_spec(list(simple_module_dict.keys())[0], None)
        assert spec is not None
        imp2.unload()
