import os
import sys
import json
import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(TESTS_DIR, "tests_data")


SIMPLE_MODULE = {
    "greeter": {
        "type": "module",
        "extension": "py",
        "code": "def hello(name):\n    return f'Hello, {name}!'\n",
    }
}

SIMPLE_PACKAGE = {
    "mathpkg": {
        "type": "package",
        "extension": "py",
        "code": "from .add import add\nfrom .mul import mul\n",
        "modules": {
            "add": {
                "type": "module",
                "extension": "py",
                "code": "def add(a, b):\n    return a + b\n",
            },
            "mul": {
                "type": "module",
                "extension": "py",
                "code": "def mul(a, b):\n    return a * b\n",
            },
        },
    }
}

NESTED_PACKAGE = {
    "outer": {
        "type": "package",
        "extension": "py",
        "code": "",
        "modules": {
            "inner": {
                "type": "package",
                "extension": "py",
                "code": "from .leaf import VALUE\n",
                "modules": {
                    "leaf": {
                        "type": "module",
                        "extension": "py",
                        "code": "VALUE = 42\n",
                    }
                },
            },
        },
    }
}


@pytest.fixture
def simple_module_dict():
    return SIMPLE_MODULE.copy()


@pytest.fixture
def simple_module_json():
    return json.dumps(SIMPLE_MODULE)


@pytest.fixture
def simple_module_bytes():
    return json.dumps(SIMPLE_MODULE).encode("utf-8")


@pytest.fixture
def simple_package_dict():
    return json.loads(json.dumps(SIMPLE_PACKAGE))


@pytest.fixture
def nested_package_dict():
    return json.loads(json.dumps(NESTED_PACKAGE))


@pytest.fixture(autouse=True)
def cleanup_sys_modules():
    """Remove any test modules from sys.modules after each test."""
    names_before = set(sys.modules.keys())
    meta_path_before = list(sys.meta_path)
    yield
    names_after = set(sys.modules.keys())
    for name in names_after - names_before:
        del sys.modules[name]
    sys.meta_path[:] = meta_path_before
