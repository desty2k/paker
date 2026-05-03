"""Dependency tree discovery for paker.

Resolves the full transitive dependency tree of a Python package
using importlib.metadata, and maps distribution names to importable
module names.
"""

import re
import importlib
import importlib.metadata
from importlib.metadata import requires


def resolve_import_names(dist_name: str) -> list[str]:
    """Map a distribution name to all its importable top-level names.

    A single distribution can provide multiple import names (e.g. ``attrs``
    provides both ``attr`` and ``attrs``). Returns them all so the packer
    can bundle the full set.
    """
    # Fast path: name variants that are directly importable.
    found: list[str] = []
    candidates = [
        dist_name,
        dist_name.replace("-", "_"),
        dist_name.replace("-", "_").lower(),
        dist_name.lower(),
    ]
    for name in candidates:
        if name not in found:
            try:
                importlib.import_module(name)
                found.append(name)
            except ImportError:
                continue

    # Walk installed distributions for additional import names this dist
    # provides (handles python-dateutil→dateutil, attrs→{attr, attrs}, etc.).
    try:
        pkg_map = importlib.metadata.packages_distributions()
    except Exception:
        return found or []
    normalised = dist_name.lower().replace("-", "_")
    for imp_name, dists in pkg_map.items():
        if imp_name in found or imp_name.startswith("_"):
            continue
        for d in dists:
            if d.lower().replace("-", "_") == normalised:
                try:
                    importlib.import_module(imp_name)
                    found.append(imp_name)
                except ImportError:
                    pass
    return found


def find_dependencies(
    package: str,
    include_extras: bool = False,
    exclude: set[str] | None = None,
) -> list[str]:
    """Find all transitive dependencies of a package as importable module names.

    Returns a list of import names (e.g. ['httpx', 'pydantic_core', ...])
    that can be passed directly to paker.dumps().

    Args:
        package: Distribution name (e.g. 'anthropic') or import name.
        include_extras: If True, include optional/extra dependencies.
        exclude: Set of import names to skip (e.g. packages that need
            data files on disk like ``{'certifi'}``).
    """
    if exclude is None:
        exclude = set()

    seen_dists: set[str] = set()
    import_names: list[str] = []

    def walk(dist_name: str):
        key = dist_name.lower()
        if key in seen_dists:
            return
        seen_dists.add(key)

        for imp_name in resolve_import_names(dist_name):
            if imp_name not in import_names and imp_name not in exclude:
                import_names.append(imp_name)

        try:
            reqs = requires(dist_name)
        except Exception:
            return
        if not reqs:
            return

        for req in reqs:
            if not include_extras and "extra ==" in req:
                continue
            dep = re.split(r"[>=<!\s;(]", req)[0].strip()
            if dep:
                walk(dep)

    walk(package)
    return import_names
