"""Tests for paker.utils."""
import sys

import pytest

from paker.utils import check_compatibility, SUPPORTED_SYSTEMS
from paker.exceptions import PakerImportError


class TestCheckCompatibility:
    def test_py_module_compatible_everywhere(self):
        data = {
            "mymod": {"type": "module", "extension": "py", "code": "x=1"}
        }
        check_compatibility(data)

    def test_incompatible_extension_raises(self):
        ext = "pyd" if not sys.platform.startswith("win") else "so"
        data = {
            "mymod": {"type": "module", "extension": ext, "code": ""}
        }
        with pytest.raises(PakerImportError, match="cannot be imported"):
            check_compatibility(data)

    def test_nested_package_checked(self):
        ext = "pyd" if not sys.platform.startswith("win") else "so"
        data = {
            "pkg": {
                "type": "package", "extension": "py", "code": "",
                "modules": {
                    "sub": {"type": "module", "extension": ext, "code": ""}
                }
            }
        }
        with pytest.raises(PakerImportError):
            check_compatibility(data)


class TestSupportedSystems:
    def test_all_extensions_have_platforms(self):
        for ext, platforms in SUPPORTED_SYSTEMS.items():
            assert len(platforms) > 0, f"extension {ext} has no platforms"

    def test_py_supported_everywhere(self):
        assert len(SUPPORTED_SYSTEMS["py"]) >= 5
