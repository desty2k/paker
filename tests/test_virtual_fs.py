"""Tests for ``paker.virtual_fs`` — the opt-in open()/os.path shim."""

from __future__ import annotations

import io
import json
import os
import sys

import pytest

import paker


TESTS_DATA = os.path.join(os.path.dirname(__file__), "tests_data")


@pytest.fixture
def loaded_testpackage():
    """Pack + load testpackage (which includes sample.json + data/*) and leave
    ``paker.virtual_fs()`` inactive so tests can opt in per case."""
    for name in list(sys.modules):
        if name == "testpackage" or name.startswith("testpackage."):
            del sys.modules[name]
    sys.path.insert(0, TESTS_DATA)
    try:
        dumped = paker.dumps("testpackage")
    finally:
        sys.path.pop(0)
    importer = paker.loads(dumped)
    try:
        yield
    finally:
        importer.unload()
        for name in list(sys.modules):
            if name == "testpackage" or name.startswith("testpackage."):
                del sys.modules[name]


class TestVirtualFSOpen:
    def test_open_rb_returns_bytes_from_bundle(self, loaded_testpackage):
        with paker.virtual_fs():
            with open("paker://testpackage/sample.json", "rb") as fp:
                data = fp.read()
        assert json.loads(data.decode())["count"] == 42

    def test_open_rt_default_utf8(self, loaded_testpackage):
        with paker.virtual_fs():
            with open("paker://testpackage/data/config.ini", "r") as fp:
                text = fp.read()
        assert "keyA=valueA" in text

    def test_open_write_mode_rejected(self, loaded_testpackage):
        with paker.virtual_fs():
            with pytest.raises(PermissionError):
                open("paker://testpackage/sample.json", "w")

    def test_missing_path_raises_filenotfound(self, loaded_testpackage):
        with paker.virtual_fs():
            with pytest.raises(FileNotFoundError):
                open("paker://testpackage/does-not-exist.json", "rb")

    def test_nonbundle_paths_pass_through(self, loaded_testpackage, tmp_path):
        real = tmp_path / "real.txt"
        real.write_text("plain")
        with paker.virtual_fs():
            with open(real, "r") as fp:
                assert fp.read() == "plain"


class TestVirtualFSPathChecks:
    def test_isfile_true_for_bundled_resource(self, loaded_testpackage):
        with paker.virtual_fs():
            assert os.path.isfile("paker://testpackage/sample.json")
            assert not os.path.isfile("paker://testpackage/missing")

    def test_isdir_true_for_virtual_dir(self, loaded_testpackage):
        with paker.virtual_fs():
            assert os.path.isdir("paker://testpackage")
            assert os.path.isdir("paker://testpackage/data")
            assert os.path.isdir("paker://testpackage/data/nested")
            assert not os.path.isdir("paker://testpackage/sample.json")

    def test_exists_covers_both(self, loaded_testpackage):
        with paker.virtual_fs():
            assert os.path.exists("paker://testpackage")
            assert os.path.exists("paker://testpackage/sample.json")
            assert not os.path.exists("paker://testpackage/missing")

    def test_listdir_iterates_virtual_dir(self, loaded_testpackage):
        with paker.virtual_fs():
            items = sorted(os.listdir("paker://testpackage"))
            assert "sample.json" in items
            assert "data" in items

    def test_listdir_errors_on_missing(self, loaded_testpackage):
        with paker.virtual_fs():
            with pytest.raises(FileNotFoundError):
                os.listdir("paker://testpackage/no-such-dir")

    def test_listdir_errors_on_file(self, loaded_testpackage):
        with paker.virtual_fs():
            with pytest.raises(NotADirectoryError):
                os.listdir("paker://testpackage/sample.json")


class TestVirtualFSScandir:
    def test_scandir_returns_diry_entries(self, loaded_testpackage):
        with paker.virtual_fs():
            with os.scandir("paker://testpackage") as it:
                entries = list(it)
            names = {e.name for e in entries}
            assert "sample.json" in names
            assert "data" in names
            types = {e.name: e.is_file() for e in entries}
            assert types["sample.json"] is True
            assert types["data"] is False

    def test_scandir_nested(self, loaded_testpackage):
        with paker.virtual_fs():
            with os.scandir("paker://testpackage/data/nested") as it:
                names = {e.name for e in it}
        assert names == {"deep.txt"}


class TestVirtualFSAbspath:
    def test_abspath_preserves_paker_prefix(self, loaded_testpackage):
        with paker.virtual_fs():
            assert os.path.abspath("paker://testpackage/x.json") == \
                "paker://testpackage/x.json"

    def test_abspath_normalises_single_slash(self, loaded_testpackage):
        with paker.virtual_fs():
            # ``paker:/`` (single slash, from naive os.path.dirname) ->
            # canonical ``paker://``.
            assert os.path.abspath("paker:/testpackage/x.json") == \
                "paker://testpackage/x.json"


class TestVirtualFSInstallLifecycle:
    def test_nested_contexts_refcount_correctly(self, loaded_testpackage):
        # ``loaded_testpackage`` auto-installs virtual_fs (default-on).
        # The shim is already active; nesting an explicit ``virtual_fs()``
        # increments the refcount and restores it on exit.
        from paker.importers._virtual_fs import _INSTALL_COUNT, _paker_open
        before = _INSTALL_COUNT
        assert open is _paker_open
        with paker.virtual_fs():
            assert open is _paker_open
            from paker.importers._virtual_fs import _INSTALL_COUNT as c1
            assert c1 == before + 1
            with paker.virtual_fs():
                from paker.importers._virtual_fs import _INSTALL_COUNT as c2
                assert c2 == before + 2
            from paker.importers._virtual_fs import _INSTALL_COUNT as c3
            assert c3 == before + 1
        from paker.importers._virtual_fs import _INSTALL_COUNT as c4
        assert c4 == before

    def test_nonbundle_paths_never_hit_shim_logic(self, loaded_testpackage, tmp_path):
        """Any path that doesn't start with ``paker://`` routes to the real
        stdlib. Missing real files should raise the normal error, not one of
        our FileNotFoundError variants."""
        missing = str(tmp_path / "definitely-not-there.txt")
        with paker.virtual_fs():
            with pytest.raises(FileNotFoundError) as excinfo:
                open(missing, "rb")
            assert excinfo.value.filename == missing


class TestStat:
    def test_stat_file(self, loaded_testpackage):
        with paker.virtual_fs():
            st = os.stat("paker://testpackage/sample.json")
        import stat as _stat
        assert _stat.S_ISREG(st.st_mode)

    def test_stat_dir(self, loaded_testpackage):
        with paker.virtual_fs():
            st = os.stat("paker://testpackage")
        import stat as _stat
        assert _stat.S_ISDIR(st.st_mode)

    def test_stat_missing(self, loaded_testpackage):
        with paker.virtual_fs():
            with pytest.raises(FileNotFoundError):
                os.stat("paker://testpackage/nope")


class TestCtypesCDLL:
    def test_shim_installed_by_default(self, loaded_testpackage):
        import ctypes
        assert ctypes.CDLL.__init__.__name__ == "_paker_cdll_init"

    def test_nonpaker_paths_pass_through(self, loaded_testpackage):
        import ctypes
        with pytest.raises(OSError):
            ctypes.CDLL("/nonexistent/path.so")
