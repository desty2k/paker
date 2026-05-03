"""Tests for bundled native-library discovery and loading."""

from __future__ import annotations

import os
import sys
import base64

import pytest

import paker
from paker._native._discovery import NativeLib, discover_native_libs
from paker._native._elf import ElfParseError, parse_needed_and_soname


# ---------------------------------------------------------------------------
# ELF parser
# ---------------------------------------------------------------------------


class TestElfParser:
    def test_rejects_non_elf(self):
        with pytest.raises(ElfParseError, match="not an ELF file"):
            parse_needed_and_soname(b"MZ" + b"\x00" * 100)

    def test_truncated_header(self):
        with pytest.raises(ElfParseError):
            parse_needed_and_soname(b"\x7fELF")

    def test_parses_real_so_when_available(self):
        """On Linux, parse an actual system shared object from the loader."""
        if sys.platform != "linux":
            pytest.skip("ELF parser smoke relies on a real .so")
        candidates = [
            "/usr/lib/x86_64-linux-gnu/libz.so.1",
            "/lib/x86_64-linux-gnu/libz.so.1",
            "/usr/lib64/libz.so.1",
        ]
        for path in candidates:
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    data = f.read()
                soname, needed = parse_needed_and_soname(data)
                assert soname is not None
                assert isinstance(needed, list)
                return
        pytest.skip("no suitable system library found for ELF parsing test")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_empty_for_non_package(self):
        import json  # stdlib, not a bundled-deps package
        result = discover_native_libs(json)
        assert result.libs == []

    def test_empty_for_simple_package(self):
        """Packages with no ``.dylibs/`` / ``*.libs/`` yield nothing."""
        # testpackage fixture has no native deps
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "tests_data"))
        try:
            import testpackage
        finally:
            sys.path.pop(0)
        result = discover_native_libs(testpackage)
        assert result.libs == []

    def test_linux_sidecar_discovered_from_extension_rpath(self, tmp_path, monkeypatch):
        """Pillow imports as PIL but its extension RPATH points to pillow.libs."""
        if sys.platform != "linux":
            pytest.skip("Linux layout helper")
        from paker._native import _discovery, _elf

        parent = tmp_path
        pkg_dir = parent / "PIL"
        pkg_dir.mkdir()
        (parent / "pillow.libs").mkdir()
        (pkg_dir / "_imaging.so").write_bytes(b"not-really-elf")
        monkeypatch.setattr(_elf, "parse_rpaths", lambda data: [
            "$ORIGIN/../pillow.libs",
        ])

        assert _discovery._linux_lib_dirs_from_extension_rpaths(
            str(pkg_dir),
        ) == [(str(parent / "pillow.libs"), "../pillow.libs")]


class TestImporterNativeLibLookup:
    def test_nested_extension_finds_top_level_native_libs(self):
        from paker.importers import JsonImporter

        payload = b"native-lib"
        importer = JsonImporter({
            "pkg": {
                "type": "package",
                "extension": "py",
                "code": "",
                "native_libs": [
                    {
                        "name": "libdep.so",
                        "layout_path": "../pkg.libs/libdep.so",
                        "code": base64.b64encode(payload).decode("ascii"),
                    }
                ],
                "modules": {
                    "sub": {
                        "type": "package",
                        "extension": "py",
                        "code": "",
                        "modules": {
                            "native": {
                                "type": "module",
                                "extension": "so",
                                "code": "",
                            }
                        },
                    }
                },
            }
        })
        try:
            assert importer._decode_bundled_libs("pkg.sub.native") == [
                ("../pkg.libs/libdep.so", payload),
            ]
        finally:
            importer.unload()

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS .dylibs layout")
    def test_pillow_dylibs(self):
        """Pillow on macOS has a delocated ``PIL/.dylibs/`` directory."""
        try:
            import PIL
        except ImportError:
            pytest.skip("Pillow not installed")
        result = discover_native_libs(PIL)
        names = {lib.name for lib in result.libs}
        # delocate always packs these for Pillow
        assert any(n.startswith("libtiff") for n in names)
        assert any(n.startswith("libjpeg") for n in names)
        for lib in result.libs:
            assert lib.layout_path.startswith("./.dylibs/")
            assert isinstance(lib.data, bytes)


# ---------------------------------------------------------------------------
# Bundle shape
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="Pillow .dylibs only on macOS")
class TestBundleShape:
    def test_native_libs_field_encrypted(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        bundle = paker.dumps("PIL", key=b"\x42" * 32, include_deps=True)
        libs = bundle["PIL"].get("native_libs", [])
        assert libs, "expected bundled dylibs on the PIL node"
        for node in libs:
            assert "name" in node
            assert "layout_path" in node
            assert "code" in node
            assert node.get("encrypted") is True

    def test_native_libs_field_unencrypted(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        bundle = paker.dumps("PIL", include_deps=True)
        libs = bundle["PIL"].get("native_libs", [])
        assert libs
        for node in libs:
            assert node.get("encrypted") is not True

    def test_opt_out(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        bundle = paker.dumps("PIL", include_native_libs=False)
        assert "native_libs" not in bundle["PIL"]


# ---------------------------------------------------------------------------
# End-to-end — load Pillow, render a PNG
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="PIL wheel layout varies on Windows")
class TestPillowRoundTrip:
    """Bundle Pillow, load it from paker, render a PNG through libjpeg/libpng.

    The PNG save path exercises ``_imaging`` → libpng which is the realistic
    C-layer use case. If bundled dylibs aren't served correctly, dlopen of
    ``_imaging.so`` fails and this raises on ``import``.
    """

    def _clear(self):
        for n in list(sys.modules):
            if n == "PIL" or n.startswith("PIL."):
                del sys.modules[n]

    def test_encrypted_roundtrip(self, tmp_path):
        try:
            import PIL  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        key = b"\xa5" * 32
        bundle = paker.dumps("PIL", key=key, include_deps=True)
        self._clear()
        try:
            with paker.loads(bundle, key=key):
                from PIL import Image
                img = Image.new("RGB", (32, 32), color=(40, 160, 220))
                out = tmp_path / "out.png"
                img.save(out, format="PNG")
                assert out.stat().st_size > 0
                with Image.open(out) as loaded:
                    loaded.load()
                    assert loaded.size == (32, 32)
                    assert loaded.getpixel((0, 0)) == (40, 160, 220)
        finally:
            self._clear()

    def test_unencrypted_roundtrip(self, tmp_path):
        try:
            import PIL  # noqa: F401
        except ImportError:
            pytest.skip("Pillow not installed")
        bundle = paker.dumps("PIL", include_deps=True)
        self._clear()
        try:
            with paker.loads(bundle):
                from PIL import Image
                img = Image.new("RGB", (16, 16), color=(255, 100, 50))
                out = tmp_path / "small.png"
                img.save(out, format="PNG")
                assert out.stat().st_size > 0
        finally:
            self._clear()
