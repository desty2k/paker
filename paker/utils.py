import sys
import base64
import marshal

from paker.importers import jsonimporter
from paker.exceptions import PakerDumpError, PakerImportError

__all__ = ["check_compatibility", "read_source_code", "get_jsonimporter_from_meta_path"]

SUPPORTED_SYSTEMS = {
    "py": ["win32", "cygwin", "freebsd", "linux", "aix", "darwin"],
    "pyc": ["win32", "cygwin", "freebsd", "linux", "aix", "darwin"],
    "dll": ["win32", "cygwin"],
    "pyd": ["win32", "cygwin"],
    "so": ["freebsd", "linux", "aix", "darwin"]
}


def check_compatibility(module_dict: dict):
    """Check if module can be loaded on current platform."""
    for key, value in module_dict.items():
        if not _is_platform_compatible(value["extension"]):
            raise PakerImportError("module {}.{} cannot be imported on {} platform".format(key, value["extension"],
                                                                                           sys.platform))
        if value["type"] == "package":
            check_compatibility(value["modules"])


def _is_platform_compatible(extension: str):
    if extension in SUPPORTED_SYSTEMS:
        if any(sys.platform.startswith(sys_name) for sys_name in SUPPORTED_SYSTEMS[extension]):
            return True
    return False


def read_source_code(path: str, compile_module: bool):
    """Read module's source code from file."""
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


def get_jsonimporter_from_meta_path():
    """Get jsonimporter instance from sys.meta_path."""
    for importer in sys.meta_path:
        if isinstance(importer, jsonimporter):
            return importer
