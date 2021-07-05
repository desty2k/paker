import os
import sys
import ctypes
import tempfile
import importlib.util
import importlib.machinery


def load_dynamic(name, path, file=None):
    """Replaces deprecated imp.load_dynamic."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return sys.modules[name]


def import_module(data, initfuncname, fullname, filename):
    """Import module from temporary directory. Used when _memimporter is not available."""
    return load_library(data, fullname, filename, dlopen=False, initfuncname=initfuncname)


def load_library(data, fullname, filename, dlopen=True, initfuncname=None):
    """Create temporary directory and import module. Used for '.pyd', '.dll' and '.so' files."""
    tempdir = tempfile.TemporaryDirectory()
    tempdir_path = tempdir.name
    filename = os.path.join(tempdir_path, filename)
    try:
        with open(filename, "wb+") as f:
            f.write(data)

        if dlopen:
            result = ctypes.CDLL(filename)
        else:
            if initfuncname:
                result = load_dynamic(initfuncname[4:], filename)
            else:
                result = load_dynamic(fullname, filename)
    except Exception as e:
        raise e
    return result
