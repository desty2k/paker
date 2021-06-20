from jsonimporter import jsonimporter

import io
import json
import types
import typing
import pkgutil
import importlib


__all__ = ["dump", "load", "dumps", "loads"]


def load(fp: io.IOBase):
    """Deserialize ``fp`` (a ``.read()``-supporting file-like object containing
        a JSON document) to a Python module."""
    return loads(fp.read())


def dump(module: typing.Union[str, types.ModuleType], fp: typing.IO[str], skip_main=True, indent=None):
    """Serialize Python module as a JSON formatted stream to ``fp`` (a
        ``.write()``-supporting file-like object)."""
    fp.write(json.dumps(dumps(module, skip_main=skip_main), indent=indent))


def loads(s: typing.Union[str, dict, bytes, bytearray]):
    """Deserialize ``s`` (a ``str``, ``dict``, ``bytes`` or ``bytearray`` instance
    containing a JSON document) to a Python module."""
    if not isinstance(s, (dict, str, bytes, bytearray)):
        raise TypeError(f'the module dict object must be dict, str, bytes or bytearray, '
                        f'not {s.__class__.__name__}')
    if isinstance(s, (bytes, bytearray)):
        s = s.decode(json.detect_encoding(s), 'surrogatepass')
    if isinstance(s, str):
        s = json.loads(s)
    return jsonimporter(s)


def dumps(module: typing.Union[str, types.ModuleType], skip_main=True):
    """Serialize Python module to a dict."""
    if type(module) is str:
        module = importlib.import_module(module)
    return {module.__name__: _dump(module, skip_main)}


def _dump(module: types.ModuleType, skip_main):
    if module.__file__.endswith("__init__.py"):
        modules = {"type": "package",
                   "code": "",
                   "modules": {}}
        with open(module.__file__, "r", encoding="utf8") as f:
            try:
                modules["code"] = f.read()
            except Exception as e:
                print("[!] Dump error in package <{} - {}> {}".format(module.__name__, module.__file__, e))
                raise e

        # serialize submodules and subpackages
        for loader, module_name, is_pkg in pkgutil.walk_packages(module.__path__):
            if module_name == "__main__" and skip_main:
                continue

            full_name = module.__name__ + '.' + module_name

            # load module
            try:
                full_module = importlib.import_module(full_name)
            except ImportError as e:
                print("[!] Dump error in package <{} - {}> {}".format(module.__name__, module.__file__, e))
                raise e

            if is_pkg:
                # recurse to every subpackage
                modules["modules"][module_name] = _dump(full_module, skip_main=skip_main)
            else:
                if full_module.__file__.endswith(".pyd"):
                    continue

                with open(full_module.__file__, "r", encoding="utf8") as f:
                    module_src = f.read()

                modules["modules"][module_name] = {"type": "module",
                                                   "code": module_src}
    else:
        modules = {"type": "module",
                   "code": ""}

        with open(module.__file__, "r") as f:
            try:
                modules["code"] = f.read()
            except Exception as e:
                print("[!] Dump error in package <{} - {}> {}".format(module.__name__, module.__file__, e))
                raise e
    return modules
