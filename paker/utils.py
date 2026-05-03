"""Shared helpers for the paker loader and packer."""

from __future__ import annotations

import sys

from paker.exceptions import PakerImportError

__all__ = ["check_compatibility"]


SUPPORTED_SYSTEMS = {
    "py": ["win32", "cygwin", "freebsd", "linux", "aix", "darwin"],
    "pyc": ["win32", "cygwin", "freebsd", "linux", "aix", "darwin"],
    "dll": ["win32", "cygwin"],
    "pyd": ["win32", "cygwin"],
    "so": ["freebsd", "linux", "aix", "darwin"],
}


def check_compatibility(module_dict: dict) -> None:
    """Raise :class:`PakerImportError` if any module in the bundle can't be
    loaded on the current platform (wrong native extension for this OS).
    """
    for key, value in module_dict.items():
        if not _is_platform_compatible(value["extension"]):
            raise PakerImportError(
                f"module {key}.{value['extension']} cannot be imported "
                f"on {sys.platform} platform"
            )
        if value["type"] == "package":
            check_compatibility(value["modules"])


def _is_platform_compatible(extension: str) -> bool:
    if extension in SUPPORTED_SYSTEMS:
        return any(
            sys.platform.startswith(name)
            for name in SUPPORTED_SYSTEMS[extension]
        )
    return False
