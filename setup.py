#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
from importlib.machinery import EXTENSION_SUFFIXES
from setuptools import setup, find_packages, Extension

# Package imports
from paker.__main__ import __version__

if sys.version_info < (3, 9):
    python_dll_name = '\\"python%d%d.dll\\"' % sys.version_info[:2]
    python_dll_name_debug = '\\"python%d%d_d.dll\\"' % sys.version_info[:2]
else:
    python_dll_name = '\"python%d%d.dll\"' % sys.version_info[:2]
    python_dll_name_debug = '\"python%d%d_d.dll\"' % sys.version_info[:2]


if "_d.pyd" in EXTENSION_SUFFIXES:
    macros = [("PYTHONDLL", python_dll_name_debug),
              # ("PYTHONCOM", '\\"pythoncom%d%d_d.dll\\"' % sys.version_info[:2]),
              ("_CRT_SECURE_NO_WARNINGS", '1')]
else:
    macros = [("PYTHONDLL", python_dll_name),
              # ("PYTHONCOM", '\\"pythoncom%d%d.dll\\"' % sys.version_info[:2]),
              ("_CRT_SECURE_NO_WARNINGS", '1'), ]
# macros.append(("Py_BUILD_CORE", '1'))

extra_compile_args = []
extra_link_args = []

extra_compile_args.append("-IC:\\Program Files\\Microsoft SDKs\\Windows\\v7.0\\Include")
extra_compile_args.append("-IC:\\Program Files (x86)\\Microsoft Visual Studio 14.0\\VC\\include")
extra_compile_args.append("-IC:\\Program Files (x86)\\Windows Kits\\10\\Include\\10.0.10586.0\\ucrt")
extra_compile_args.append("/DSTANDALONE")

if 0:
    # enable this to debug a release build
    extra_compile_args.append("/Od")
    extra_compile_args.append("/Z7")
    extra_link_args.append("/DEBUG")
    macros.append(("VERBOSE", "1"))

_memimporter = Extension("_memimporter",
                         ["source/_memimporter.c",
                          "source/MemoryModule.c",
                          "source/MyLoadLibrary.c",
                          "source/actctx.c",
                          ],
                         libraries=["user32", "shell32"],
                         define_macros=macros + [("STANDALONE", "1")],
                         extra_compile_args=extra_compile_args,
                         extra_link_args=extra_link_args,
                         )

# build memimporter on Windows
ext_modules = []
if sys.platform.startswith("win32"):
    ext_modules = [_memimporter]

with open("README.md", "r", encoding="utf-8") as f:
    long_desc = f.read()

setup(
    name='paker',
    version=__version__,
    packages=find_packages(),
    url='https://github.com/desty2k/paker',
    license='MIT',
    author='Wojciech Wentland',
    author_email='wojciech.wentland@int.pl',
    description='Import Python modules from JSON documents',
    long_description_content_type='text/markdown',
    python_requires='>=3.6',
    zip_safe=False,  # don't use eggs
    long_description=long_desc,
    entry_points={
        'console_scripts': [
            'paker=paker.__main__:main_entry',
        ],
    },
    ext_modules=ext_modules,
    classifiers=[
        'Development Status :: 4 - Beta',

        'Environment :: Console',

        'License :: OSI Approved :: MIT License',

        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',

        'Programming Language :: Python :: Implementation :: CPython',
        'Programming Language :: Python :: Implementation :: PyPy',

        'Intended Audience :: Developers',
        'Intended Audience :: Information Technology',

        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: Software Development :: Build Tools',
        'Topic :: Software Development :: Testing',

    ],
    keywords=['import',
              'loader',
              'finder',
              'importer',
              'json',
              'memory',
              'memimporter'],
)
