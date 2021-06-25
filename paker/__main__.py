"""# Paker

[![Build](https://github.com/desty2k/paker/actions/workflows/build.yml/badge.svg)](https://github.com/desty2k/paker/actions/workflows/build.yml)
[![Version](https://img.shields.io/pypi/v/paker)](https://pypi.org/project/paker/)
[![Version](https://img.shields.io/pypi/dm/paker)](https://pypi.org/project/paker/)


Paker is module for importing Python packages/modules from JSON documents.
It was inspired by [httpimporter](https://github.com/operatorequals/httpimport).


## Installation
From PyPI

```shell
pip install paker
```

From source

```shell
git clone https://github.com/desty2k/paker.git
cd paker
pip install .
```

## Usage

### In Python script

You can import Python module from dict.

```python

import paker
import logging

MODULE = {"somemodule": {"type": "module", "code": "fun = lambda x: x**2"}}
logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    with paker.loads(MODULE) as loader:
        # somemodule will be available only in this context
        from somemodule import fun
        assert fun(2), 4
        assert fun(5), 25
        print("6**2 is {}".format(fun(6)))
        print("It works!")

```

It is also possible to import modules from `.json` files.
In this example we will use paker to serialize [mss](https://pypi.org/project/mss/) package.

```python

import paker
import logging

file = "mss.json"
logging.basicConfig(level=logging.NOTSET)

# install mss using `pip install mss`
# serialize module
with open(file, "w+") as f:
    paker.dump("mss", f, indent=4)

# now you can uninstall mss using `pip uninstall mss -y`
# load package back from dump file
with open(file, "r") as f:
    loader = paker.load(f)

import mss
with mss.mss() as sct:
    sct.shot()

# remove loader and clean the cache
loader.unload()

# this will throw error
import mss

```

### CLI
Paker can also work as a standalone script.
To dump module to JSON dict use `dump` command:

```shell
paker dump mss
```

To recreate module from JSON dict use `load`:

```shell
paker load mss.json
```

Show all modules and packages in `.json` file

```shell
paker list mss.json
```

## How it works

Paker implements its own [importer](https://docs.python.org/3/glossary.html#term-importer) called `jsonimporter`.
When importing modules or packages Python iterates over importers in `sys.meta_path` and calls `find_module` method on each object.
If the importer returns `self`, it means that the module can be imported and `None` means that importer did not find searched package.
If any importer has confirmed the ability to import module, Python executes another method on it - `load_module`.

To dump module or package to JSON document, Paker recursively iterates over modules and creates dict with
code and type of each module and submodules if object is package.

## Bugs

Loading modules from `.pyd` files does not work.


"""


import os
import sys
import json
import logging
import argparse
from paker import dump

__version__ = "0.3.4"


def _dump(args):
    sys.path.insert(0, os.path.abspath("."))
    mod = args.module[0]
    output_path = args.output if args.output else mod
    indent = args.indent if args.indent else None

    if not output_path.endswith(".json"):
        output_path = output_path + ".json"

    with open(output_path, "w+") as f:
        dump(mod, f, indent=indent)


def _list(args):
    path = args.module[0]
    with open(path, "r") as f:
        mod_dict = json.loads(f.read())
    return _recursive_list(mod_dict)


def _recursive_list(mod_dict: dict, parent=""):
    for key, value in mod_dict.items():
        if value["type"] == "package":
            if parent:
                key = parent + "." + key
            print("P {}".format(key))
            _recursive_list(value["modules"], parent=key)
        else:
            if parent:
                key = parent + "." + key
            print("M {}".format(key))


def _load(args):
    path = args.module[0]
    with open(path, "r") as f:
        mod_dict = json.loads(f.read())
    return _recursive_load(mod_dict)


def _recursive_load(mod_dict: dict):
    for key, value in mod_dict.items():
        if value["type"] == "package":
            os.makedirs(key, exist_ok=True)
            old_dir = os.getcwd()
            os.chdir(key)
            with open("__init__.py", "w+") as f:
                f.write(value["code"])
            _recursive_load(value["modules"])
            os.chdir(old_dir)
        else:
            with open(key + ".py", "w+") as f:
                f.write(value["code"])


def _parser():
    parser = argparse.ArgumentParser(
        prog='paker',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Import Python modules from JSON documents.",
        epilog='See "paker <command> -h" for more information '
               'on a specific command.'
    )

    parser.add_argument('-V', '--version', action='version', version='v{}'.format(__version__),
                        help='print version and exit')

    subparsers = parser.add_subparsers(
        title='Available commands',
        metavar=''
    )

    # find and dump module to JSON
    cparser = subparsers.add_parser(
        'dump',
        aliases=['d'],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help='Dump package to JSON dict',
        description="Dump package to JSON dict"
    )
    cparser.add_argument('module', nargs=1, metavar='MODULE',
                         help='Module name')
    cparser.add_argument('-O', '--output', metavar='PATH',
                         help='Output file name, default is module name')
    cparser.add_argument('-I', '--indent', metavar='INDENT', type=int, default=None,
                         help='Indent in JSON document, default is %(default)s')

    cparser.set_defaults(func=_dump)

    # recreating module tree from JSON
    cparser = subparsers.add_parser(
        'load',
        aliases=['l'],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help='Recreate module from JSON dict',
        description="Recreate module from JSON dict"
    )
    cparser.add_argument('module', nargs=1, metavar='MODULE',
                         help='Path to JSON document with module')
    cparser.add_argument('-O', '--output', default='.', metavar='PATH',
                         help='Output directory path, default is "%(default)s"')
    cparser.set_defaults(func=_load)

    # list modules and packages in json file
    cparser = subparsers.add_parser(
        'list',
        aliases=['ls'],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help='List modules and packages in JSON document',
        description="List modules and packages in JSON document"
    )
    cparser.add_argument('module', nargs=1, metavar='MODULE',
                         help='Path to JSON document with module')
    cparser.set_defaults(func=_list)
    return parser


def main(argv):
    parser = _parser()
    args = parser.parse_args(argv)
    if not hasattr(args, 'func'):
        parser.print_help()
        return

    args.func(args)


def main_entry():
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)-8s %(message)s',
    )
    main(sys.argv[1:])


if __name__ == '__main__':
    main_entry()
