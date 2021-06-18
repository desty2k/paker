# Paker
Paker is module for serializing and deserializing Python modules and packages. 
It was inspired by [httpimporter](https://github.com/operatorequals/httpimport).

## How it works
Paker dumps entire package structure to JSON dict. 
When loading package back, package is recreated with its submodules and subpackages.

## Instalation
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

### CLI

To dump module to JSON dict use `dump` command:

```shell
paker dump mss
```

To recreate module from JSON dict use `load`:

```shell
paker load mss.json
```

### In Python script
In this example we will use paker to serialize [mss](https://pypi.org/project/mss/) package.

```python

import json
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

# remove loader and clean cache
loader.unload()

# this will throw error
import mss

```
Check example directory for more scripts.

## Bugs

Loading modules from `.pyd` files does not work.
