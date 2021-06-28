#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages

# Package imports
from paker.__main__ import __version__

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
              'memory'],
)
