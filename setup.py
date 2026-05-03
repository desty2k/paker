"""Setup script for building paker's C extensions."""

import sys
from setuptools import setup, Extension

_paker_crypto = Extension(
    "_paker_crypto",
    sources=[
        "csrc/crypto/_paker_crypto.c",
        "csrc/crypto/aes.c",
    ],
    define_macros=[
        ("AES256", "1"),
        ("CTR", "1"),
        ("CBC", "0"),
        ("ECB", "0"),
    ],
)

ext_modules = [_paker_crypto]

if sys.platform == "win32":
    from importlib.machinery import EXTENSION_SUFFIXES

    python_dll = f'"python{sys.version_info[0]}{sys.version_info[1]}.dll"'
    python_dll_debug = f'"python{sys.version_info[0]}{sys.version_info[1]}_d.dll"'

    if "_d.pyd" in EXTENSION_SUFFIXES:
        macros = [
            ("PYTHONDLL", python_dll_debug),
            ("_CRT_SECURE_NO_WARNINGS", "1"),
        ]
    else:
        macros = [
            ("PYTHONDLL", python_dll),
            ("_CRT_SECURE_NO_WARNINGS", "1"),
        ]

    _memimporter = Extension(
        "_memimporter",
        sources=[
            "csrc/memimporter/_memimporter.c",
            "csrc/memimporter/MemoryModule.c",
            "csrc/memimporter/MyLoadLibrary.c",
            "csrc/memimporter/actctx.c",
        ],
        libraries=["user32", "shell32"],
        define_macros=macros + [("STANDALONE", "1")],
        extra_compile_args=["/DSTANDALONE"],
    )
    ext_modules.append(_memimporter)

setup(ext_modules=ext_modules)
