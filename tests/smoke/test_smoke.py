"""Smoke tests: load pre-packed JSON bundles and exercise each package.

Run in a clean venv with only ``paker`` and ``pytest`` installed — none of
the target packages should be available on ``sys.path``. The bundles must
be generated first by ``pack.py`` (or cached from a previous run / CI step).

    pytest tests/smoke/test_smoke.py -v
"""

from __future__ import annotations

import json
import os
import sys

import pytest

import paker

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLES_DIR = os.path.join(HERE, "bundles")
KEY = b"\x42" * 32

# -----------------------------------------------------------------------
# Per-package exercises. Each returns a short description on success.
# -----------------------------------------------------------------------

EXERCISES: dict[str, str] = {
    "click": (
        "import click, io\n"
        "grp = click.Group('test')\n"
        "assert grp.name == 'test'\n"
        "buf = io.StringIO()\n"
        "click.echo('hello', file=buf)\n"
        "assert buf.getvalue() == 'hello\\n'\n"
    ),
    "psutil": (
        "import psutil\n"
        "assert psutil.cpu_count() > 0\n"
    ),
    "jinja2": (
        "from jinja2 import Environment\n"
        "assert Environment().from_string('{{ n }}').render(n='ok') == 'ok'\n"
    ),
    "pydantic": (
        "from pydantic import BaseModel\n"
        "class M(BaseModel): x: int\n"
        "assert M(x=1).x == 1\n"
    ),
    "requests": (
        "import requests\n"
        "s = requests.Session()\n"
        "req = requests.Request('GET', 'http://x.invalid', params={'a': '1'})\n"
        "assert '?a=1' in s.prepare_request(req).url\n"
        "s.close()\n"
    ),
    "yaml": (
        "import yaml\n"
        "assert yaml.safe_load(yaml.safe_dump({'k': 1})) == {'k': 1}\n"
    ),
    "rich": (
        "import rich.console\n"
        "c = rich.console.Console(record=True, width=40)\n"
        "c.print('hi')\n"
        "assert 'hi' in c.export_text()\n"
    ),
    "sqlalchemy": (
        "from sqlalchemy import create_engine, text\n"
        "e = create_engine('sqlite:///:memory:')\n"
        "with e.connect() as c:\n"
        "    r = c.execute(text('SELECT 1+1')).scalar()\n"
        "    assert r == 2\n"
    ),
    "numpy": (
        "import numpy as np\n"
        "assert np.array([1, 2, 3]).sum() == 6\n"
    ),
    "boto3": (
        "import boto3\n"
        "s = boto3.Session(region_name='us-east-1')\n"
        "assert s.region_name == 'us-east-1'\n"
        "assert 's3' in s.get_available_services()\n"
    ),
    "anthropic": (
        "from anthropic import Anthropic\n"
        "c = Anthropic(api_key='sk-ant-fake')\n"
        "assert hasattr(c.messages, 'create')\n"
    ),
    "flask": (
        "from flask import Flask, jsonify\n"
        "app = Flask('test')\n"
        "with app.test_request_context():\n"
        "    resp = jsonify(ok=True)\n"
        "    assert resp.status_code == 200\n"
    ),
    "fastapi": (
        "from fastapi import FastAPI\n"
        "from fastapi.routing import APIRoute\n"
        "app = FastAPI()\n"
        "@app.get('/x')\n"
        "def x(): return {'v': 1}\n"
        "routes = [r for r in app.routes if isinstance(r, APIRoute)]\n"
        "assert len(routes) == 1 and routes[0].path == '/x'\n"
    ),
    "PIL": (
        "from PIL import Image\n"
        "import io\n"
        "img = Image.new('RGB', (4, 4), color=(255, 0, 0))\n"
        "buf = io.BytesIO()\n"
        "img.save(buf, format='PNG')\n"
        "buf.seek(0)\n"
        "assert Image.open(buf).getpixel((0, 0)) == (255, 0, 0)\n"
    ),
    "mcp": (
        "from mcp.types import Tool\n"
        "t = Tool(name='add', inputSchema={'type': 'object', 'properties': {}})\n"
        "assert t.name == 'add'\n"
    ),
    "pypdfium2": (
        "import pypdfium2 as pdfium\n"
        "pdf = pdfium.PdfDocument.new()\n"
        "pdf.new_page(200, 300)\n"
        "assert len(pdf) == 1\n"
        "pdf.close()\n"
    ),
}


def _bundle_path(pkg: str, variant: str) -> str:
    return os.path.join(BUNDLES_DIR, f"{pkg}_{variant}.json")


def _available_bundles() -> list[tuple[str, str]]:
    """Return (pkg, variant) pairs for all bundles on disk."""
    result = []
    for pkg in EXERCISES:
        for variant in ("plain", "enc"):
            if os.path.isfile(_bundle_path(pkg, variant)):
                result.append((pkg, variant))
    return result


_BASELINE_MODULES: set[str] = set()


@pytest.fixture(autouse=True, scope="session")
def _capture_baseline():
    """Record which modules exist before any smoke test runs."""
    _BASELINE_MODULES.update(sys.modules)


_KEEP_PREFIXES = ("paker", "pytest", "_pytest", "pluggy", "py", "Pygments",
                  "pygments", "importlib", "json", "io", "os", "sys",
                  "pathlib", "typing", "collections", "re", "enum",
                  "functools", "contextlib", "abc", "warnings", "logging",
                  "threading", "hashlib", "hmac", "base64", "struct",
                  "errno", "stat", "builtins", "marshal", "pkgutil",
                  "cryptography")


def _clear_all_loaded() -> None:
    """Remove third-party modules loaded by previous tests, keep stdlib + paker."""
    for name in list(sys.modules):
        if name in _BASELINE_MODULES:
            continue
        if any(name == p or name.startswith(p + ".") for p in _KEEP_PREFIXES):
            continue
        del sys.modules[name]


def _load_and_exercise(pkg: str, variant: str) -> None:
    path = _bundle_path(pkg, variant)
    if not os.path.isfile(path):
        pytest.skip(f"bundle not found: {path}")
    with open(path) as f:
        bundle = json.load(f)
    key = KEY if variant == "enc" else None
    disk_policy = os.environ.get("PAKER_SMOKE_DISK_POLICY")
    _clear_all_loaded()
    try:
        with paker.loads(bundle, key=key, disk_policy=disk_policy):
            exec(compile(EXERCISES[pkg], f"<smoke:{pkg}>", "exec"))
    finally:
        _clear_all_loaded()


# -----------------------------------------------------------------------
# Parametrized tests — one per (package, variant) pair
# -----------------------------------------------------------------------

_cases = _available_bundles()
if not _cases:
    _cases = [(pkg, v) for pkg in EXERCISES for v in ("plain", "enc")]


@pytest.mark.parametrize(
    "pkg,variant",
    _cases,
    ids=[f"{p}-{v}" for p, v in _cases],
)
def test_smoke(pkg: str, variant: str) -> None:
    _load_and_exercise(pkg, variant)
