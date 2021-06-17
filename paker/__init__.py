"""Paker is utility for dumping and loading Python modules from zip files.
"""

from paker.jsonimporter import jsonimporter

import json
import types
import typing
import pkgutil
import importlib

__version__ = "0.3"
__all__ = ["dump", "load", "__version__"]


def load(json_dict: typing.Union[str, dict]):
    """Load Python module from bytes stream containing zip file."""
    if type(json_dict) is str:
        json_dict = json.loads(json_dict)
    return jsonimporter(json_dict)


def dump(module: typing.Union[str, types.ModuleType], skip_main=True):
    """Dump Python module to zip file"""
    if type(module) is str:
        module = importlib.import_module(module)
    return {module.__name__: _dump(module, skip_main)}


def _dump(module: types.ModuleType, skip_main):
    modules = {"type": "package",
               "code": "",
               "modules": {}}

    with open(module.__file__, "rb") as f:
        try:
            modules["code"] = f.read().decode()
        except Exception as e:
            print("[!] Dump error in package <{} - {}> {}".format(module.__name__, module.__file__, e))
            raise e

    # serialize submodules and subpackages
    for loader, module_name, is_pkg in pkgutil.walk_packages(module.__path__):
        if module_name == "__main__" and skip_main:
            continue

        full_name = module.__name__ + '.' + module_name
        if is_pkg:
            # recurse to every subpackage
            modules["modules"][module_name](_dump(full_name, skip_main=skip_main))
        else:
            # load module
            try:
                full_module = importlib.import_module(full_name)
            except ImportError:
                continue

            if full_module.__file__.endswith(".pyd"):
                continue

            with open(full_module.__file__, "rb") as f:
                module_src = f.read()

            modules["modules"][module_name] = {"type": "module",
                                               "code": module_src.decode()}
    return modules

