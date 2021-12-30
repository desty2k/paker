import os
import sys
import atexit
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


def import_module(modname, pathname, initfuncname, finder, spec):
    """Create temporary file on disk and import module. Used for '.pyd', '.dll' and '.so' files."""
    logger.info("importing {} from temporary file".format(modname))
    if modname in module_cache:
        return module_cache[modname][1]

    if os.path.isfile(paker_tempdir):
        os.remove(paker_tempdir)
    if not os.path.isdir(paker_tempdir):
        os.makedirs(paker_tempdir, exist_ok=True)

    # .pyd file path
    filepath = os.path.join(paker_tempdir, os.path.split(pathname)[-1])
    try:
        with open(filepath, "wb+") as f:
            f.write(finder(pathname))
        logger.debug("created temporary file {} for module {}".format(filepath, modname))
        if initfuncname:
            result = load_dynamic(initfuncname[7:], filepath)
        else:
            result = load_dynamic(modname, filepath)
    except Exception as e:
        raise e

    if len(module_cache) == 0:
        logger.debug("registering cleanup atexit function")
        atexit.register(_cleanup)
    module_cache[modname] = (filepath, result)
    return result


# TODO
# Windows - delete %TEMP%/paker/ dir using bat script
# Linux - delete /tmp/paker/ dir using bash/sh script
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
