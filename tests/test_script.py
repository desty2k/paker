"""Integration test: dump a real package to JSON and re-import it."""
import os
import sys
import json
import pytest

import paker

TESTS_DATA = os.path.join(os.path.dirname(__file__), "tests_data")
PACKAGE_NAME = "testpackage"


def test_dump_and_reload(tmp_path):
    sys.path.insert(0, TESTS_DATA)
    output = tmp_path / f"{PACKAGE_NAME}.json"
    try:
        dumped = paker.dumps(PACKAGE_NAME)
        serialized = json.dumps(dumped)
        output.write_text(serialized)

        for name in list(sys.modules):
            if name.startswith(PACKAGE_NAME):
                del sys.modules[name]

        with open(output, "r") as f:
            with paker.load(f) as loader:
                mod = loader.load_module(PACKAGE_NAME)
                assert mod.__name__ == PACKAGE_NAME

        import testpackage
        assert "Fibonacci" in dir(testpackage)
        assert testpackage.Fibonacci.fib(5) == [0, 1, 1, 2, 3]
        assert testpackage.Fibonacci.fib_odd(5) == [1, 1, 3]
    finally:
        sys.path.pop(0)
