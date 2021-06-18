#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages

# Package imports
from paker.__main__ import __version__

setup(
    name='paker',
    version=__version__,
    packages=find_packages(),
    url='https://github.com/desty2k/paker',
    license='MIT',
    author='Wojciech Wentland',
    author_email='wojciech.wentland@int.pl',
    description='Serialize Python modules and packages',
    long_description_content_type='text/x-rst',
    python_requires='>=3.5',
    zip_safe=False,  # don't use eggs
    entry_points={
        'console_scripts': [
            'paker=paker.__main__:main_entry',
        ],
    },
)
