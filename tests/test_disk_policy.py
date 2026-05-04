"""Tests for the unified disk_policy parameter."""
import os
import sys
import pytest

import paker
from paker._policy import DiskPolicy, resolve_disk_policy, map_disk_policy
from paker._loaders._base import NativeLoaderPolicy
from paker._loaders._resource_paths import AsFileStrategy
from paker._loaders._tempfile import TempFileLoader

TESTS_DATA = os.path.join(os.path.dirname(__file__), "tests_data")


def _pack(**kwargs):
    sys.path.insert(0, TESTS_DATA)
    try:
        return paker.dumps("testpackage", **kwargs)
    finally:
        sys.path.pop(0)


def _clear():
    for n in list(sys.modules):
        if n == "testpackage" or n.startswith("testpackage."):
            del sys.modules[n]


class TestDiskPolicyEnum:
    def test_values(self):
        assert DiskPolicy.MEMORY.value == "memory"
        assert DiskPolicy.AUTO.value == "auto"
        assert DiskPolicy.DISK.value == "disk"

    def test_string_construction(self):
        assert DiskPolicy("memory") is DiskPolicy.MEMORY
        assert DiskPolicy("auto") is DiskPolicy.AUTO
        assert DiskPolicy("disk") is DiskPolicy.DISK


class TestResolveDiskPolicy:
    def test_none_returns_none(self):
        assert resolve_disk_policy(None) is None

    def test_enum_passthrough(self):
        assert resolve_disk_policy(DiskPolicy.DISK) is DiskPolicy.DISK

    def test_string_case_insensitive(self):
        assert resolve_disk_policy("MEMORY") is DiskPolicy.MEMORY
        assert resolve_disk_policy("Memory") is DiskPolicy.MEMORY
        assert resolve_disk_policy("disk") is DiskPolicy.DISK

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="invalid disk_policy"):
            resolve_disk_policy("bogus")


class TestMapDiskPolicy:
    def test_memory(self):
        af, nl = map_disk_policy(DiskPolicy.MEMORY)
        assert af is AsFileStrategy.STRICT
        assert nl is NativeLoaderPolicy.AUTO

    def test_auto(self):
        af, nl = map_disk_policy(DiskPolicy.AUTO)
        assert af is AsFileStrategy.AUTO
        assert nl is NativeLoaderPolicy.AUTO

    def test_disk(self):
        af, nl = map_disk_policy(DiskPolicy.DISK)
        assert af is AsFileStrategy.TEMPFILE
        assert nl is NativeLoaderPolicy.DISK


class TestDiskPolicyIntegration:
    def test_memory(self):
        bundle = _pack()
        _clear()
        importer = paker.loads(bundle, disk_policy="memory")
        try:
            assert importer._as_file_strategy is AsFileStrategy.STRICT
        finally:
            importer.unload()
            _clear()

    def test_auto(self):
        bundle = _pack()
        _clear()
        importer = paker.loads(bundle, disk_policy="auto")
        try:
            assert importer._as_file_strategy is AsFileStrategy.AUTO
        finally:
            importer.unload()
            _clear()

    def test_disk(self):
        bundle = _pack()
        _clear()
        importer = paker.loads(bundle, disk_policy="disk")
        try:
            assert importer._as_file_strategy is AsFileStrategy.TEMPFILE
            assert isinstance(importer._native_loader, TempFileLoader)
        finally:
            importer.unload()
            _clear()

    def test_default_is_memory_behavior(self):
        bundle = _pack()
        _clear()
        importer = paker.loads(bundle)
        try:
            assert importer._as_file_strategy is AsFileStrategy.STRICT
        finally:
            importer.unload()
            _clear()


class TestDiskPolicyOverrides:
    def test_as_file_override_wins(self):
        bundle = _pack()
        _clear()
        importer = paker.loads(
            bundle, disk_policy="memory", as_file_strategy="tempfile",
        )
        try:
            assert importer._as_file_strategy is AsFileStrategy.TEMPFILE
        finally:
            importer.unload()
            _clear()

    def test_native_loader_override_wins(self):
        bundle = _pack()
        _clear()
        importer = paker.loads(
            bundle, disk_policy="disk", native_loader="auto",
        )
        try:
            assert not isinstance(importer._native_loader, TempFileLoader)
        finally:
            importer.unload()
            _clear()

    def test_both_overrides_ignore_policy(self):
        bundle = _pack()
        _clear()
        importer = paker.loads(
            bundle,
            disk_policy="disk",
            as_file_strategy="strict",
            native_loader="auto",
        )
        try:
            assert importer._as_file_strategy is AsFileStrategy.STRICT
            assert not isinstance(importer._native_loader, TempFileLoader)
        finally:
            importer.unload()
            _clear()

    def test_nested_policy_override_creates_separate_importer(self):
        bundle = _pack()
        _clear()
        first = paker.loads(bundle, native_loader="auto")
        second = None
        try:
            second = paker.loads(bundle, native_loader="disk")
            assert second is not first
            assert isinstance(second._native_loader, TempFileLoader)
        finally:
            if second is not None:
                second.unload()
            first.unload()
            _clear()


class TestDiskPolicyEnvIgnored:
    def test_env_var_not_consulted(self, monkeypatch):
        monkeypatch.setenv("PAKER_DISK_POLICY", "disk")
        bundle = _pack()
        _clear()
        importer = paker.loads(bundle)
        try:
            assert importer._as_file_strategy is AsFileStrategy.STRICT
        finally:
            importer.unload()
            _clear()


class TestDiskPolicyMpInitializer:
    def test_forwarded_to_child(self):
        bundle = _pack()
        fn, args = paker.mp_initializer(bundle, disk_policy="disk")
        _clear()
        fn(*args)
        from paker import _get_active_importer
        importer = _get_active_importer()
        try:
            assert isinstance(importer._native_loader, TempFileLoader)
            assert importer._as_file_strategy is AsFileStrategy.TEMPFILE
        finally:
            importer.unload()
            _clear()
