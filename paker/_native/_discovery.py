"""Pack-time discovery of bundled native libraries.

Handles three real-world wheel layouts for bundled native dependencies:

* **macOS delocate**: ``<pkg>/.dylibs/*.dylib`` — subdirectory of the package.
  The package's ``.so`` files reference them via ``@loader_path/.dylibs/...``.
* **Linux auditwheel**: ``<pkg>.libs/*.so.*`` — sibling directory at the
  ``site-packages`` root. Referenced via ``$ORIGIN/../<pkg>.libs/...``.
* **Windows delvewheel**: ``<pkg>.libs/*.dll`` or ``<pkg>/<pkg>.libs/*.dll``.

Returns a :class:`DiscoveredLibs` with per-library payload and metadata.
The caller (:func:`paker.dumps`) turns it into the on-bundle representation.
"""

from __future__ import annotations

import logging
import os
import sys
import types
from dataclasses import dataclass, field


logger = logging.getLogger(__name__)


@dataclass
class NativeLib:
    """A single bundled native library destined for the paker bundle."""

    name: str                      # basename, e.g. "libtiff.6.dylib"
    data: bytes                    # raw file bytes
    deps: list[str] = field(default_factory=list)
    soname: str | None = None      # ELF DT_SONAME if parsed; else None
    # Where to place this lib relative to the *.so that references it at
    # load time. Examples:
    #   "./.dylibs/libtiff.6.dylib"  (macOS)
    #   "../numpy.libs/libopenblas.so"  (Linux sibling)
    layout_path: str = ""


@dataclass
class DiscoveredLibs:
    """Result of walking a package for bundled native libraries."""

    libs: list[NativeLib] = field(default_factory=list)
    # Maps main extension basename → list of lib names it depends on (direct
    # refs only; transitive closure is implicit in the libs graph). Used by
    # the Linux loader to know which libs to preload before dlopen'ing a
    # given extension.
    ext_deps: dict[str, list[str]] = field(default_factory=dict)


def discover_native_libs(
    module: types.ModuleType,
) -> DiscoveredLibs:
    """Walk the package's directories for bundled native libraries.

    Does nothing for non-packages. For packages, inspects the platform-
    appropriate layout and returns a :class:`DiscoveredLibs` the packer
    can serialize into the bundle.
    """
    result = DiscoveredLibs()
    paths = getattr(module, "__path__", None)
    if not paths:
        return result

    pkg_name = module.__name__
    for path_entry in paths:
        if not os.path.isdir(path_entry):
            continue

        if sys.platform == "darwin":
            _discover_dylibs_macos(path_entry, result)
        elif sys.platform.startswith("linux"):
            _discover_libs_linux(path_entry, pkg_name, result)
        elif sys.platform == "win32":
            _discover_libs_windows(path_entry, pkg_name, result)

    return result


# ---------------------------------------------------------------------------
# macOS — ``<pkg>/.dylibs/*.dylib``
# ---------------------------------------------------------------------------


def _discover_dylibs_macos(pkg_dir: str, result: DiscoveredLibs) -> None:
    dylibs_dir = os.path.join(pkg_dir, ".dylibs")
    if not os.path.isdir(dylibs_dir):
        return
    for name in sorted(os.listdir(dylibs_dir)):
        if name.startswith("."):
            continue
        src = os.path.join(dylibs_dir, name)
        if not os.path.isfile(src):
            continue
        try:
            with open(src, "rb") as f:
                data = f.read()
        except OSError as e:
            logger.debug("skipping bundled dylib %s: %s", name, e)
            continue
        # On macOS we rely on dyld's @loader_path resolution — no dep
        # parsing needed. All files in .dylibs/ are packed as-is, sorted
        # by name (delocate already curates the set).
        result.libs.append(
            NativeLib(
                name=name,
                data=data,
                layout_path=f"./.dylibs/{name}",
            )
        )


# ---------------------------------------------------------------------------
# Linux — ``<pkg>.libs/*.so*`` (sibling at site-packages root)
# ---------------------------------------------------------------------------


def _discover_libs_linux(
    pkg_dir: str, pkg_name: str, result: DiscoveredLibs,
) -> None:
    # The package's top name derives the sibling dir. ``pkg_name`` may
    # contain dots for subpackages; only walk sibling dirs when this is
    # actually the top-level package (dirs are keyed off the distribution
    # name at site-packages root).
    if "." in pkg_name:
        return
    candidates = _linux_lib_dirs_from_extension_rpaths(pkg_dir)

    from paker._native._elf import ElfParseError, parse_needed_and_soname

    for sibling, layout_prefix in candidates:
        for name in sorted(os.listdir(sibling)):
            if name.startswith("."):
                continue
            src = os.path.join(sibling, name)
            if not os.path.isfile(src):
                continue
            try:
                with open(src, "rb") as f:
                    data = f.read()
            except OSError as e:
                logger.debug("skipping bundled lib %s: %s", name, e)
                continue
            soname, needed = None, []
            try:
                soname, needed = parse_needed_and_soname(data)
            except ElfParseError as e:
                logger.debug("could not parse %s: %s", name, e)
            result.libs.append(
                NativeLib(
                    name=name,
                    data=data,
                    deps=needed,
                    soname=soname or name,
                    layout_path=f"{layout_prefix}/{name}",
                )
            )


def _linux_lib_dirs_from_extension_rpaths(
    pkg_dir: str,
) -> list[tuple[str, str]]:
    """Return auditwheel sidecar dirs referenced by package extension RPATHs.

    Auditwheel records sidecar library locations in each extension's
    ``DT_RPATH`` / ``DT_RUNPATH`` using ``$ORIGIN``. Resolving those entries
    is more reliable than guessing names: Pillow imports as ``PIL`` but uses
    ``pillow.libs``, while numpy uses ``numpy.libs``.
    """
    from paker._native._elf import ElfParseError, parse_rpaths

    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    pkg_parent = os.path.dirname(pkg_dir)

    for root, dirs, files in os.walk(pkg_dir):
        dirs[:] = [
            d for d in dirs
            if d != "__pycache__" and not d.startswith(".")
        ]
        for filename in files:
            if not filename.endswith((".so", ".pyd")) and ".so." not in filename:
                continue
            path = os.path.join(root, filename)
            try:
                with open(path, "rb") as f:
                    rpaths = parse_rpaths(f.read())
            except (OSError, ElfParseError):
                continue
            origin = os.path.dirname(path)
            for rpath in rpaths:
                resolved = _resolve_elf_origin_path(origin, rpath)
                if not os.path.isdir(resolved):
                    continue
                if not os.path.basename(resolved).endswith(".libs"):
                    continue
                real = os.path.realpath(resolved)
                if real in seen:
                    continue
                seen.add(real)
                layout = os.path.relpath(real, pkg_parent).replace(os.sep, "/")
                result.append((real, f"../{layout}"))

    return result


def _resolve_elf_origin_path(origin: str, value: str) -> str:
    return os.path.abspath(
        value
        .replace("${ORIGIN}", origin)
        .replace("$ORIGIN", origin)
    )


# ---------------------------------------------------------------------------
# Windows — ``<pkg>.libs/*.dll`` (sibling) or ``<pkg>/<pkg>.libs/*.dll``
# ---------------------------------------------------------------------------


def _discover_libs_windows(
    pkg_dir: str, pkg_name: str, result: DiscoveredLibs,
) -> None:
    # Two real layouts: sibling (delvewheel recent) and inner (older).
    candidates: list[tuple[str, str]] = []
    if "." not in pkg_name:
        parent = os.path.dirname(pkg_dir)
        sibling = os.path.join(parent, f"{pkg_name}.libs")
        if os.path.isdir(sibling):
            candidates.append((sibling, f"../{pkg_name}.libs"))
    inner = os.path.join(pkg_dir, f"{pkg_name}.libs")
    if os.path.isdir(inner):
        candidates.append((inner, f"./{pkg_name}.libs"))

    for src_dir, layout_prefix in candidates:
        for name in sorted(os.listdir(src_dir)):
            if name.startswith("."):
                continue
            src = os.path.join(src_dir, name)
            if not os.path.isfile(src):
                continue
            try:
                with open(src, "rb") as f:
                    data = f.read()
            except OSError as e:
                logger.debug("skipping bundled lib %s: %s", name, e)
                continue
            result.libs.append(
                NativeLib(
                    name=name,
                    data=data,
                    layout_path=f"{layout_prefix}/{name}",
                )
            )
