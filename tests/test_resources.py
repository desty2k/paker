"""Tests for paker resource bundling and ``importlib.resources`` support."""

from __future__ import annotations

import os
import sys
import json
import pytest

import paker


TESTS_DATA = os.path.join(os.path.dirname(__file__), "tests_data")


@pytest.fixture(autouse=True)
def _reset_testpackage():
    for name in list(sys.modules):
        if name == "testpackage" or name.startswith("testpackage."):
            del sys.modules[name]
    yield
    for name in list(sys.modules):
        if name == "testpackage" or name.startswith("testpackage."):
            del sys.modules[name]


def _pack(**kwargs):
    sys.path.insert(0, TESTS_DATA)
    try:
        return paker.dumps("testpackage", **kwargs)
    finally:
        sys.path.pop(0)


class TestPacker:
    def test_top_level_resource_captured(self):
        bundle = _pack()
        assert "sample.json" in bundle["testpackage"]["resources"]

    def test_nested_resource_paths_use_posix_separator(self):
        bundle = _pack()
        keys = list(bundle["testpackage"]["resources"])
        assert "data/config.ini" in keys
        assert "data/nested/deep.txt" in keys
        assert all("\\" not in k for k in keys)

    def test_subpackage_resources_not_leaked_into_parent(self):
        bundle = _pack()
        # Even if subpackage had data files they'd be under
        # bundle["testpackage"]["modules"]["subpackage"]["resources"], never
        # bleeding into the parent's resource dict.
        parent_resources = bundle["testpackage"].get("resources", {})
        assert not any(
            k.startswith("subpackage/") for k in parent_resources
        )

    def test_py_and_pyc_files_excluded(self):
        bundle = _pack()
        for key in bundle["testpackage"].get("resources", {}):
            assert not key.endswith((".py", ".pyc", ".pyo"))

    def test_include_resources_false_skips_resources(self):
        bundle = _pack(include_resources=False)
        assert "resources" not in bundle["testpackage"]


class TestResourcesAPI:
    def test_importlib_resources_files_read_bytes(self):
        bundle = _pack()
        with paker.loads(bundle):
            from importlib import resources
            payload = resources.files("testpackage").joinpath("sample.json").read_bytes()
            assert json.loads(payload.decode("utf-8"))["count"] == 42

    def test_importlib_resources_files_read_text(self):
        bundle = _pack()
        with paker.loads(bundle):
            from importlib import resources
            text = resources.files("testpackage").joinpath("data", "config.ini").read_text()
            assert "keyA=valueA" in text

    def test_importlib_resources_files_iterdir_top_level(self):
        bundle = _pack()
        with paker.loads(bundle):
            from importlib import resources
            root = resources.files("testpackage")
            children = {c.name for c in root.iterdir()}
            # Virtual dir ``data`` alongside the real file ``sample.json``.
            assert "sample.json" in children
            assert "data" in children

    def test_importlib_resources_files_nested_iterdir(self):
        bundle = _pack()
        with paker.loads(bundle):
            from importlib import resources
            root = resources.files("testpackage")
            nested = root.joinpath("data", "nested")
            assert nested.is_dir()
            assert {c.name for c in nested.iterdir()} == {"deep.txt"}

    def test_is_file_vs_is_dir(self):
        bundle = _pack()
        with paker.loads(bundle):
            from importlib import resources
            root = resources.files("testpackage")
            assert root.joinpath("sample.json").is_file()
            assert not root.joinpath("sample.json").is_dir()
            assert root.joinpath("data").is_dir()
            assert not root.joinpath("data").is_file()

    def test_missing_resource_raises_file_not_found(self):
        bundle = _pack()
        with paker.loads(bundle):
            from importlib import resources
            with pytest.raises(FileNotFoundError):
                resources.files("testpackage").joinpath("does-not-exist").read_bytes()

    def test_pkgutil_get_data_roundtrip(self):
        bundle = _pack()
        with paker.loads(bundle):
            import pkgutil
            import testpackage  # noqa: F401 — triggers loader bind
            data = pkgutil.get_data("testpackage", "sample.json")
            assert json.loads(data)["hello"] == "world"

    def test_pkgutil_get_data_nested_path(self):
        bundle = _pack()
        with paker.loads(bundle):
            import pkgutil
            import testpackage  # noqa: F401
            data = pkgutil.get_data("testpackage", "data/nested/deep.txt")
            assert data.rstrip() == b"nested-content"


class TestEncryptedResources:
    def test_encrypted_bundle_round_trip(self):
        KEY = b"\x42" * 32
        bundle = _pack(key=KEY)

        # Bundle blob is not base64 — it's the encrypt()-produced string.
        blob = bundle["testpackage"]["resources"]["sample.json"]
        assert isinstance(blob, str)

        with paker.loads(bundle, key=KEY):
            from importlib import resources
            payload = resources.files("testpackage").joinpath("sample.json").read_bytes()
            assert json.loads(payload.decode("utf-8"))["hello"] == "world"

    def test_wrong_key_fails_resource_read(self):
        KEY = b"\x42" * 32
        WRONG = b"\x37" * 32
        bundle = _pack(key=KEY)
        # Module-level decrypt guards are tested elsewhere; here we want the
        # resource decrypt path specifically. Manually invoke the reader on
        # a wrong-keyed importer without going through the module-import
        # machinery (which would short-circuit on HMAC before we reach the
        # resource path).
        importer = paker.loads(bundle, key=WRONG)
        try:
            from paker.importers._resources import BundleResources
            reader = BundleResources(importer, "testpackage")
            with pytest.raises(Exception):
                reader.files().joinpath("sample.json").read_bytes()
        finally:
            importer.unload()
