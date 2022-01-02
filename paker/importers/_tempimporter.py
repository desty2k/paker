import os
import sys
import atexit
import shutil
import logging
import tempfile
import subprocess
import importlib.util
import importlib.machinery

module_cache = {}
logger = logging.getLogger("tempimporter")
TEMPDIR = tempfile.gettempdir()
PAKER_TEMPDIR = os.path.join(TEMPDIR, "paker")
PAKER_BAT_PATH = os.path.join(TEMPDIR, "paker.bat")


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

    if os.path.isfile(PAKER_TEMPDIR):
        os.remove(PAKER_TEMPDIR)
    if not os.path.isdir(PAKER_TEMPDIR):
        os.makedirs(PAKER_TEMPDIR, exist_ok=True)

    filepath = os.path.join(PAKER_TEMPDIR, os.path.split(pathname)[-1])
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


def _cleanup():
    """Clean up files on script exit. Log exceptions."""
    try:
        if sys.platform.startswith("win32"):
            _delete_windows()
        else:
            _delete_linux()
    except Exception as e:
        logger.error(f"failed to clean up files at exit: '{e}'")


def _delete_windows():
    """Create '.bat' file in %TEMP% directory and start it.
    When started, script waits 1s, kills Python process,
    removes paker temporary directory and deletes itself from disk.

    Used as cleanup function on win32 platform.
    """
    with open(PAKER_BAT_PATH, "w+") as f:
        f.write("""
timeout 1
taskkill /PID {} /F
rd /s /q "{}"
(goto) 2>nul & del "%~f0"
        """.format(os.getpid(), os.path.normpath(PAKER_TEMPDIR)))
    subprocess.Popen(f.name, stdout=None, stderr=None, stdin=None, close_fds=True,
                     creationflags=subprocess.CREATE_NO_WINDOW, shell=False)


def _delete_linux():
    """Remove paker tempdir from disk.

    Used as cleanup function on non win32 platforms.
    """
    shutil.rmtree(PAKER_TEMPDIR)
