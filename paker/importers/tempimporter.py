import os
import sys
import atexit
import ctypes
import shutil
import logging
import tempfile
import importlib.util
import importlib.machinery

module_cache = {}
logger = logging.getLogger("tempimporter")
paker_tempdir = os.path.join(tempfile.gettempdir(), "paker")


def load_dynamic(name, path, file=None):
    """Replaces deprecated imp.load_dynamic."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return sys.modules[name]


def import_module(data, initfuncname, fullname, filename):
    """Import module from temporary file. Used when _memimporter is not available."""
    return load_library(data, fullname, filename, dlopen=False, initfuncname=initfuncname)


def load_library(data, fullname, filename, dlopen=True, initfuncname=None):
    """Create temporary file on disk and import module. Used for '.pyd', '.dll' and '.so' files."""
    logger.info("importing {} from temporary file".format(fullname))
    if fullname in module_cache:
        return module_cache[fullname][1]

    if os.path.isfile(paker_tempdir):
        os.remove(paker_tempdir)
    if not os.path.isdir(paker_tempdir):
        os.makedirs(paker_tempdir, exist_ok=True)

    filename = os.path.join(paker_tempdir, filename)
    try:
        with open(filename, "wb+") as f:
            f.write(data)
        logger.debug("created temporary file {} for module {}".format(filename, fullname))

        if dlopen:
            result = ctypes.CDLL(filename)
        else:
            if initfuncname:
                result = load_dynamic(initfuncname[4:], filename)
            else:
                result = load_dynamic(fullname, filename)
    except Exception as e:
        raise e

    if len(module_cache) == 0:
        logger.debug("registering cleanup atexit function")
        atexit.register(_cleanup)
    module_cache[fullname] = (filename, result)
    return result


def _cleanup():
    """Python does not support unloading dynamic libraries in runtime, so
    cleanup is ran once at exit."""
    for module in module_cache.values():
        info = "module '{}' from '{}'".format(module[1].__name__, module[0])
        try:
            logger.debug("removing {}".format(info))
            os.remove(module[0])
        except Exception as e:
            logger.error("failed to remove {}: {}".format(info, e))
    shutil.rmtree(paker_tempdir, ignore_errors=True)
