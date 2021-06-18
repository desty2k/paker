import os
import sys
import pytest

TESTS_DATA = "tests/tests_data"
OUTPUT_FILENAME = "testpackage.json"
PACKAGE_NAME = "testpackage"

sys.path.insert(0, os.path.join(os.getcwd(), TESTS_DATA))


@pytest.mark.order(1)
def test_script_dumps():
    # import paker
    import paker

    # dump
    dumped = paker.dumps(PACKAGE_NAME)

    # serialize dict to str
    import json
    dumped = json.dumps(dumped)

    with open(OUTPUT_FILENAME, "w+") as f:
        f.write(dumped)


@pytest.mark.order(2)
def test_script_loads():
    # import paker
    import paker

    # read file with serialized module
    with open(OUTPUT_FILENAME, "r") as f:
        dumped = f.read()

    # load module using loader
    with paker.loads(dumped) as loader:
        mod = loader.load_module("testpackage")
        assert mod.__name__ == "testpackage"

    # import module (standard way)
    import testpackage

    # testpackage should have Fibonacci submodule
    assert "Fibonacci" in dir(testpackage)
    assert testpackage.Fibonacci.fib(5) == [0, 1, 1, 2, 3]
    assert testpackage.Fibonacci.fib_odd(5) == [1, 1, 3]


@pytest.fixture(scope="session", autouse=True)
def cleanup(request):
    """Cleanup a testing directory once we are finished."""
    def remove_test_files():
        os.remove(OUTPUT_FILENAME)
    request.addfinalizer(remove_test_files)
