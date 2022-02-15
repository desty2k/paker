import io
import json
import types
import typing
import pkgutil
import importlib
from importlib.machinery import SourceFileLoader

from paker.importers import jsonimporter
from paker.exceptions import PakerDumpError, PakerImportError
from paker.utils import check_compatibility, read_source_code, get_jsonimporter_from_meta_path

__all__ = ["dump", "load", "dumps", "loads", "__version__"]
__version__ = "0.7.1"


def load(fp: io.IOBase, overwrite: bool = False):
    """Deserialize ``fp`` (a ``.read()``-supporting file-like object containing
        a JSON document) to a Python module.

    If ``overwrite`` is false, then paker will not overwrite existing modules.
    """
    return loads(fp.read(), overwrite=overwrite)


def dump(module: typing.Union[str, types.ModuleType],
         fp: typing.IO[str],
         skip_modules: typing.List[typing.Union[str, types.ModuleType]] = None,
         indent: int = None,
         compile_modules: bool = False):
    """Serialize Python module as a JSON formatted stream to ``fp`` (a
        ``.write()``-supporting file-like object).

    If ``skip_main`` is true then ``__main__`` files will not be serialized.

    ``indent``, if specified, paker will generate JSON document with additional indent.

    If ``compile_modules`` is true then all serialized modules will be compiled with ``optimize`` flag.
    """
    fp.write(json.dumps(dumps(module, skip_modules=skip_modules, compile_modules=compile_modules), indent=indent))


def loads(s: typing.Union[str, dict, bytes, bytearray], overwrite: bool = False):
    """Deserialize ``s`` (a ``str``, ``dict``, ``bytes`` or ``bytearray`` instance
    containing a JSON document) to a Python module.

    If ``overwrite`` is false, then paker will not overwrite existing modules.
    """
    if not isinstance(s, (dict, str, bytes, bytearray)):
        raise TypeError(f'the module dict object must be dict, str, bytes or bytearray, '
                        f'not {s.__class__.__name__}')
    if isinstance(s, (bytes, bytearray)):
        s = s.decode(json.detect_encoding(s), 'surrogatepass')
    if isinstance(s, str):
        s = json.loads(s)
    check_compatibility(s)
    importer = get_jsonimporter_from_meta_path()
    if importer is not None:
        modules = list(s.keys())
        for module_name in modules:
            if importer.find_module(module_name) is not None:
                if overwrite:
                    importer.add_module(module_name, s.get(module_name))
            else:
                importer.add_module(module_name, s.get(module_name))
    else:
        importer = jsonimporter(s)
    return importer


def dumps(module: typing.Union[str, types.ModuleType],
          skip_modules: typing.List[typing.Union[str, types.ModuleType]] = None,
          compile_modules: bool = False):
    """Serialize Python module to a dict.

    If ``skip_modules`` is specified, then paker will skip serializing these modules.
    If ``compile_modules`` is true then all serialized modules will be compiled with ``optimize`` flag.
    """
    if type(module) is str:
        module = importlib.import_module(module)
    if not isinstance(module.__loader__, SourceFileLoader):
        raise PakerDumpError(f"module loader must be SourceFileLoader (got '{module.__loader__.__class__.__name__}')")
    if skip_modules is None:
        skip_modules = ["__main__"]
    return {module.__package__: _dump(module, skip_modules, compile_modules)}


def _dump(module: types.ModuleType, skip_modules, compile_modules):
    if module.__file__.endswith("__init__.py"):
        extension, code = read_source_code(module.__file__, compile_modules)
        modules = {"type": "package",
                   "extension": extension,
                   "code": code,
                   "modules": {}}

        # serialize submodules and subpackages
        for loader, module_name, is_pkg in pkgutil.walk_packages(module.__path__):
            if module_name in skip_modules:
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
                modules["modules"][module_name] = _dump(full_module, skip_modules=skip_modules,
                                                        compile_modules=compile_modules)
            else:
                extension, code = read_source_code(full_module.__file__, compile_modules)
                modules["modules"][module_name] = {"type": "module",
                                                   "extension": extension,
                                                   "code": code}
    else:
        extension, code = read_source_code(module.__file__, compile_modules)
        modules = {"type": "module",
                   "extension": extension,
                   "code": code}
    return modules
