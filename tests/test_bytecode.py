"""Tests for importing compiled bytecode (.pyc) modules."""
import sys
import json
import base64
import marshal
import pytest

import paker


def _make_pyc_module(name: str, source: str) -> dict:
    """Compile source to bytecode and return a paker module dict entry."""
    code_obj = compile(source, f"<{name}>", "exec", optimize=2)
    encoded = base64.b64encode(marshal.dumps(code_obj)).decode()
    return {
        name: {
            "type": "module",
            "extension": "pyc",
            "code": encoded,
        }
    }


class TestBytecodeImport:
    def test_simple_pyc_module(self):
        data = _make_pyc_module("pyc_simple", "VALUE = 42\n")
        with paker.loads(data) as imp:
            import pyc_simple
            assert pyc_simple.VALUE == 42

    def test_pyc_with_function(self):
        data = _make_pyc_module("pyc_func", "def double(x):\n    return x * 2\n")
        with paker.loads(data) as imp:
            import pyc_func
            assert pyc_func.double(21) == 42

    def test_pyc_package(self):
        source_init = "from .calc import compute\n"
        source_calc = "def compute(x):\n    return x + 1\n"

        init_code = base64.b64encode(
            marshal.dumps(compile(source_init, "<init>", "exec", optimize=2))
        ).decode()
        calc_code = base64.b64encode(
            marshal.dumps(compile(source_calc, "<calc>", "exec", optimize=2))
        ).decode()

        data = {
            "pyc_pkg": {
                "type": "package",
                "extension": "pyc",
                "code": init_code,
                "modules": {
                    "calc": {
                        "type": "module",
                        "extension": "pyc",
                        "code": calc_code,
                    }
                },
            }
        }
        with paker.loads(data) as imp:
            import pyc_pkg
            assert pyc_pkg.compute(9) == 10
