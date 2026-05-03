"""Tests for the paker CLI (__main__.py)."""
import os
import sys
import json
import pytest

from paker.__main__ import main


class TestCLIVersion:
    def test_version_output(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["-V"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert captured.out.startswith("v")

    def test_no_args_shows_help(self, capsys):
        main([])
        captured = capsys.readouterr()
        assert "paker" in captured.out.lower()


class TestCLIDump:
    def test_dump_creates_file(self, tmp_path):
        data_dir = os.path.join(os.path.dirname(__file__), "tests_data")
        sys.path.insert(0, data_dir)
        out = str(tmp_path / "testpackage.json")
        try:
            main(["dump", "testpackage", "-O", out])
            assert os.path.exists(out)
            with open(out) as f:
                data = json.load(f)
            assert "testpackage" in data
        finally:
            sys.path.pop(0)

    def test_dump_with_indent(self, tmp_path):
        data_dir = os.path.join(os.path.dirname(__file__), "tests_data")
        sys.path.insert(0, data_dir)
        out = str(tmp_path / "indented.json")
        try:
            main(["dump", "testpackage", "-O", out, "-I", "4"])
            with open(out) as f:
                content = f.read()
            assert "    " in content
        finally:
            sys.path.pop(0)


class TestCLIList:
    def test_list_modules(self, tmp_path, capsys):
        pkg = {
            "mypkg": {
                "type": "package", "extension": "py", "code": "",
                "modules": {
                    "sub": {"type": "module", "extension": "py", "code": "x=1"}
                }
            }
        }
        f = tmp_path / "pkg.json"
        f.write_text(json.dumps(pkg))
        main(["list", str(f)])
        captured = capsys.readouterr()
        assert "P mypkg" in captured.out
        assert "M mypkg.sub" in captured.out


class TestCLILoad:
    def test_load_recreates_files(self, tmp_path):
        pkg = {
            "mypkg": {
                "type": "package", "extension": "py", "code": "# init\n",
                "modules": {
                    "sub": {"type": "module", "extension": "py", "code": "x = 1\n"}
                }
            }
        }
        f = tmp_path / "pkg.json"
        f.write_text(json.dumps(pkg))
        outdir = tmp_path / "output"
        main(["load", str(f), "-O", str(outdir)])

        init_path = outdir / "mypkg" / "__init__.py"
        sub_path = outdir / "mypkg" / "sub.py"
        assert init_path.exists()
        assert sub_path.exists()
        assert init_path.read_text() == "# init\n"
        assert sub_path.read_text() == "x = 1\n"
