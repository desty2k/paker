import io
import json
import types
import base64
import typing
import pkgutil
import marshal
import importlib

from paker.importers import jsonimporter
from paker.exceptions import PakerDumpError

__all__ = ["dump", "load", "dumps", "loads"]


def load(fp: io.IOBase):
    """Deserialize ``fp`` (a ``.read()``-supporting file-like object containing
        a JSON document) to a Python module."""
    return loads(fp.read())


def dump(module: typing.Union[str, types.ModuleType], fp: typing.IO[str], skip_main=True, indent=None,
         compile_modules=False):
    """Serialize Python module as a JSON formatted stream to ``fp`` (a
        ``.write()``-supporting file-like object)."""
    fp.write(json.dumps(dumps(module, skip_main=skip_main, compile_modules=compile_modules), indent=indent))


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


def dumps(module: typing.Union[str, types.ModuleType], skip_main=True, compile_modules=False):
    """Serialize Python module to a dict."""
    if type(module) is str:
        module = importlib.import_module(module)
    return {module.__package__: _dump(module, skip_main, compile_modules)}


def _dump(module: types.ModuleType, skip_main, compile_modules):
    if module.__file__.endswith("__init__.py"):
        extension, code = _read_module_code(module.__file__, compile_modules)
        modules = {"type": "package",
                   "extension": extension,
                   "code": code,
                   "modules": {}}

        # serialize submodules and subpackages
        for loader, module_name, is_pkg in pkgutil.walk_packages(module.__path__):
            if module_name == "__main__" and skip_main:
                continue

            # skip nested packages, they will be processed recursively later
            if len(module_name.split(".")) > 1:
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
                modules["modules"][module_name] = _dump(full_module, skip_main=skip_main,
                                                        compile_modules=compile_modules)
            else:
                extension, code = _read_module_code(full_module.__file__, compile_modules)
                modules["modules"][module_name] = {"type": "module",
                                                   "extension": extension,
                                                   "code": code}
    else:
        extension, code = _read_module_code(module.__file__, compile_modules)
        modules = {"type": "module",
                   "extension": extension,
                   "code": code}
    return modules


def _read_module_code(path: str, compile_module):
    extension = path.split(".")[-1]
    if extension == "py":
        with open(path, "r", encoding="utf-8") as f:
            code = f.read()
            if compile_module:
                try:
                    # try to compile and optimize module
                    code = base64.b64encode(marshal.dumps(compile(code, path, "exec", optimize=2))).decode()
                    extension = "pyc"
                except Exception as e:
                    raise PakerDumpError("failed to compile module {}: {}".format(path, e))

    elif extension in ("pyc", "dll", "pyd", "so"):
        with open(path, "rb") as f:
            code = base64.b64encode(f.read()).decode()

    else:
        raise PakerDumpError("unknown module extension {} (expected py, pyc, dll, pyd, so)".format(extension))

    return extension, code
