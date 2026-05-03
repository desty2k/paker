"""In-memory ``importlib.resources`` support for paker bundles.

paker packs non-Python files (JSON data, PEM certs, Jinja templates, ...) as
a per-package ``resources: {posix_relpath: encoded_bytes}`` map alongside the
module's ``code`` entry. At load time, :class:`BundleResources` exposes that
map to library code through the standard ``importlib.resources`` API:

    from importlib import resources
    data = resources.files("mypkg").joinpath("data", "x.json").read_bytes()
    # or legacy:
    from pkgutil import get_data
    data = get_data("mypkg", "data/x.json")

Both paths route through :class:`paker.importers.JsonImporter` which
forwards to :class:`BundleResources`.

The implementation treats the flat ``{relpath: bytes}`` dict as a virtual
filesystem: any key prefix ending in ``/`` is an implicit directory. The
dict is decrypted lazily — ``.read_bytes()`` on a specific resource reads
and decrypts only that entry.
"""

from __future__ import annotations

import base64
import contextlib
import io
import posixpath
from collections.abc import Iterator
import sys
from importlib.resources import as_file

if sys.version_info >= (3, 12):
    from importlib.resources.abc import Traversable, TraversableResources
else:
    from importlib.abc import Traversable, TraversableResources


class BundleResources(TraversableResources):
    """Per-package resource reader. Returned from ``JsonImporter.get_resource_reader``."""

    def __init__(
        self,
        importer,
        package_fullname: str,
    ):
        self._importer = importer
        self._package_fullname = package_fullname

    def files(self) -> "Traversable":
        return _BundleTraversable(
            self._importer,
            self._package_fullname,
            "",
            is_root=True,
        )


class _BundleTraversable(Traversable):
    """A ``Traversable`` backed by the bundle's per-package resources dict.

    ``rel`` is the path within the package, POSIX-separated, without a
    leading slash. ``is_root`` marks the package root so ``.name`` reports
    the package's simple name rather than an empty string.
    """

    __slots__ = ("_importer", "_package_fullname", "_rel", "_is_root")

    def __init__(
        self,
        importer,
        package_fullname: str,
        rel: str,
        *,
        is_root: bool = False,
    ):
        self._importer = importer
        self._package_fullname = package_fullname
        self._rel = rel
        self._is_root = is_root

    # -- Traversable protocol --

    @property
    def name(self) -> str:
        if self._is_root:
            return self._package_fullname.rsplit(".", 1)[-1]
        return posixpath.basename(self._rel)

    def is_dir(self) -> bool:
        if self._is_root or self._rel == "":
            return True
        resources = self._importer._resources_for(self._package_fullname)
        if resources is None:
            return False
        prefix = self._rel.rstrip("/") + "/"
        return any(key.startswith(prefix) for key in resources)

    def is_file(self) -> bool:
        resources = self._importer._resources_for(self._package_fullname)
        if resources is None:
            return False
        return self._rel in resources

    def iterdir(self) -> Iterator["_BundleTraversable"]:
        resources = self._importer._resources_for(self._package_fullname)
        if resources is None:
            return iter(())
        prefix = self._rel.rstrip("/") + "/" if self._rel else ""
        seen: set[str] = set()
        children: list[tuple[str, str]] = []
        for key in resources:
            if not key.startswith(prefix) or key == prefix[:-1]:
                continue
            tail = key[len(prefix):]
            if not tail:
                continue
            head, sep, _ = tail.partition("/")
            if head in seen:
                continue
            seen.add(head)
            children.append((head, prefix + head))
        # Keep output deterministic across platforms.
        children.sort(key=lambda pair: pair[0])
        for _name, child_rel in children:
            yield _BundleTraversable(
                self._importer, self._package_fullname, child_rel,
            )

    def joinpath(self, *descendants: str) -> "_BundleTraversable":
        parts = [p for p in descendants if p]
        if not parts:
            return self
        rel = "/".join([self._rel, *parts]) if self._rel else "/".join(parts)
        # Normalise ``a//b`` / ``a/./b`` / ``a/../b`` to a canonical form.
        rel = posixpath.normpath(rel)
        if rel in (".", ""):
            rel = ""
        return _BundleTraversable(
            self._importer, self._package_fullname, rel,
        )

    # ``/`` operator — same as joinpath, protocol requires both.
    def __truediv__(self, child: str) -> "_BundleTraversable":
        return self.joinpath(child)

    def open(self, mode: str = "r", *args, **kwargs):
        if "b" in mode:
            return io.BytesIO(self.read_bytes())
        encoding = kwargs.pop("encoding", None) or (args[0] if args else "utf-8")
        return io.StringIO(self.read_bytes().decode(encoding))

    def read_bytes(self) -> bytes:
        if self._is_root or self._rel == "":
            raise IsADirectoryError(self._package_fullname)
        resources = self._importer._resources_for(self._package_fullname)
        if resources is None or self._rel not in resources:
            raise FileNotFoundError(self._rel)
        blob = resources[self._rel]
        return _decode_resource(self._importer, blob)

    def read_text(self, encoding: str | None = None, errors: str | None = None) -> str:
        data = self.read_bytes()
        return data.decode(encoding or "utf-8", errors or "strict")

    def __repr__(self) -> str:
        return f"<BundleTraversable {self._package_fullname}:{self._rel or '<root>'}>"


def _materialize_tree(traversable: _BundleTraversable, base) -> None:
    """Recursively write a traversable tree to a real directory."""
    target = base / traversable.name
    if traversable.is_dir():
        target.mkdir(exist_ok=True)
        for child in traversable.iterdir():
            _materialize_tree(child, target)
    else:
        target.write_bytes(traversable.read_bytes())


@as_file.register(_BundleTraversable)
@contextlib.contextmanager
def _as_file_bundle(traversable: _BundleTraversable):
    """Serve ``as_file(bundle_traversable)`` from memory where possible.

    ``importlib.resources.as_file`` is a ``functools.singledispatch`` on
    Python 3.10+. Registering our concrete ``_BundleTraversable`` here means
    library code that passes a paker-backed resource to ``as_file`` gets a
    memory-backed fd path (Linux memfd, macOS ``/dev/fd/N``) instead of the
    stdlib's default tempfile copy.

    The strategy (``STRICT`` / ``AUTO`` / ``TEMPFILE``) comes from the owning
    :class:`~paker.importers.JsonImporter` and governs the Windows fallback.
    Directory traversables fall through to the stdlib's ``_temp_dir`` path
    — we have no way to expose a whole subtree as a single fd.
    """
    if traversable.is_dir():
        import tempfile
        import pathlib
        with tempfile.TemporaryDirectory() as tmpdir:
            base = pathlib.Path(tmpdir)
            for child in traversable.iterdir():
                _materialize_tree(child, base)
            yield base
        return

    from paker._loaders._resource_paths import resource_path

    strategy = traversable._importer._as_file_strategy
    data = traversable.read_bytes()
    with resource_path(data, suffix=_as_file_suffix(traversable), strategy=strategy) as path:
        yield path


def _as_file_suffix(traversable: _BundleTraversable) -> str:
    """Preserve the resource's file suffix so consumers (OpenSSL, etc.) see ``.pem`` etc."""
    name = traversable.name
    dot = name.rfind(".")
    return name[dot:] if dot > 0 else ""


def _decode_resource(importer, blob: str) -> bytes:
    """Decode a single resource entry from the bundle.

    Encrypted bundles go through the same AES-CTR + HMAC path as module code;
    unencrypted bundles are plain base64. The returned ``bytes`` is what the
    library sees — callers don't need to worry about scrubbing it since the
    data is already a public resource (certs, JSON configs, templates).
    """
    if importer._key is not None:
        from paker._crypto import (
            _aes_ctr_fallback,
            _c_decrypt_into_bytearray,
            _derive_keys,
            _validate_key,
            _verify_and_split,
            scrub,
        )
        _validate_key(importer._key)
        enc_key, mac_key = _derive_keys(importer._key)
        payload = _verify_and_split(blob, mac_key)
        dest = bytearray()
        if _c_decrypt_into_bytearray is not None:
            _c_decrypt_into_bytearray(payload, enc_key, dest)
        else:
            dest.extend(_aes_ctr_fallback(enc_key, payload[:16], payload[16:]))
        try:
            return bytes(dest)
        finally:
            scrub(dest)
    return base64.b64decode(blob)
