#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup

# Package imports
from paker import __version__

setup(
    name='paker',
    version=__version__,
    url='https://github.com/desty2k/paker',
    license='MIT',
    author='Wojciech Wentland',
    author_email='wojciech.wentland@int.pl',
    description='Serialize Python modules and packages',
    long_description_content_type='text/x-rst',
    python_requires='>=3.5',
    zip_safe=False,  # don't use eggs
)
