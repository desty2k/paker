"""Tests for concurrent/multiple loaders."""
import sys
import pytest

import paker
from paker.importers import JsonImporter


class TestMultipleLoaders:
    def test_two_independent_loaders(self):
        mod_a = {
            "mod_a": {
                "type": "module", "extension": "py",
                "code": "A = 'alpha'\n",
            }
        }
        mod_b = {
            "mod_b": {
                "type": "module", "extension": "py",
                "code": "B = 'beta'\n",
            }
        }
        with paker.loads(mod_a):
            with paker.loads(mod_b):
                import mod_a
                import mod_b
                assert mod_a.A == "alpha"
                assert mod_b.B == "beta"

        assert "mod_a" not in sys.modules
        assert "mod_b" not in sys.modules

    def test_loader_with_cross_dependency(self):
        base = {
            "base_mod": {
                "type": "module", "extension": "py",
                "code": "BASE = 100\n",
            }
        }
        dependent = {
            "dep_mod": {
                "type": "module", "extension": "py",
                "code": "from base_mod import BASE\nDEP = BASE + 1\n",
            }
        }
        with paker.loads(base):
            with paker.loads(dependent):
                import dep_mod
                assert dep_mod.DEP == 101

    def test_same_module_name_different_contexts(self):
        v1 = {
            "versioned": {
                "type": "module", "extension": "py",
                "code": "VERSION = 1\n",
            }
        }
        v2 = {
            "versioned": {
                "type": "module", "extension": "py",
                "code": "VERSION = 2\n",
            }
        }

        with paker.loads(v1):
            import versioned
            assert versioned.VERSION == 1

        assert "versioned" not in sys.modules

        with paker.loads(v2):
            import versioned
            assert versioned.VERSION == 2


class TestReusedImporter:
    def test_add_multiple_modules(self):
        first = {
            "first": {
                "type": "module", "extension": "py",
                "code": "X = 1\n",
            }
        }
        second = {
            "second": {
                "type": "module", "extension": "py",
                "code": "Y = 2\n",
            }
        }

        imp = paker.loads(first)
        paker.loads(second)

        import first as f
        import second as s
        assert f.X == 1
        assert s.Y == 2
        imp.unload()
