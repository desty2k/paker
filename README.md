# Paker

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

Check example directory for more scripts.


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
