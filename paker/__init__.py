"""Paker is utility for dumping and loading Python modules from zip files.
"""

from .spooled_zipimporter import zipimporter
from .exception import PakerImportError

import os
import types
import typing
import zipfile
import pathlib
import importlib

_mod_type = type(typing)
__version__ = "0.2"
__all__ = ["dump", "load", "__version__"]


def loads(zip_stream: typing.Union[bytes, bytearray]):
    """Load Python module from bytes stream containing zip file."""
    if not isinstance(zip_stream, (bytes, bytearray)):
        raise PakerImportError("the zip object must be bytes or bytearray, not {}".format(
            zip_stream.__class__.__name__))
    return zipimporter(zip_stream)


def load(io_object: typing.IO):
    return loads(io_object.read())


def dump(module: typing.Union[str, types.ModuleType], target_dir=".", suffix=".zip"):
    """Dump Python module to zip file"""
    if type(module) is str:
        module = importlib.import_module(module)
    mod = pathlib.Path(module.__file__).resolve()
    mod_dir = mod.parent

    with zipfile.ZipFile(target_dir + "/" + mod_dir.name + suffix, "w", zipfile.ZIP_DEFLATED) as output_zip:
        for root, dirs, files in os.walk(mod_dir):
            for file in files:
                output_zip.write(os.path.join(root, file),
                                 os.path.relpath(os.path.join(root, file),
                                                 os.path.join(mod_dir, '..')))
    return pathlib.Path(output_zip.filename).resolve()
