"""Native-library discovery and bundling for paker.

When a Python package ships extensions that link against bundled native
dependencies (Pillow's ``PIL/.dylibs/``, numpy's ``numpy.libs/``, etc.),
this subpackage handles:

* **Discovery** — ``discover_native_libs(module)`` walks the package layout
  appropriate for the current platform and returns a graph of libs.
* **ELF parsing** — ``_elf.parse_needed_and_soname`` extracts ``DT_NEEDED``
  and ``DT_SONAME`` from an ELF shared object without any C dependency.
  Used at load time to topologically sort Linux dependency loads.
* **Bundle format helpers** — content-addressed blob storage that avoids
  duplicating identical dylibs across packages.

See :mod:`paker._loaders._linux` / :mod:`paker._loaders._macos` for the
load-time side.
"""

from paker._native._discovery import (
    DiscoveredLibs,
    NativeLib,
    discover_native_libs,
)

__all__ = ["DiscoveredLibs", "NativeLib", "discover_native_libs"]
