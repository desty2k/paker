# Paker

[![Build](https://github.com/desty2k/paker/actions/workflows/build.yml/badge.svg)](https://github.com/desty2k/paker/actions/workflows/build.yml)
[![Version](https://img.shields.io/pypi/v/paker)](https://pypi.org/project/paker/)
[![Version](https://img.shields.io/pypi/dm/paker)](https://pypi.org/project/paker/)


Paker is module for importing Python packages/modules from dictionaries and JSON formatted documents. 
It was inspired by [httpimporter](https://github.com/operatorequals/httpimport).

__Important:__ Since v0.6.0 `paker` supports importing `.pyd` and `.dll` modules directly from memory. 
This was achieved by using `_memimporter` from [py2exe](https://github.com/py2exe/py2exe) project.
Importing `.so` files on Linux still requires writing them to disk.

## Installation
From PyPI

```shell
pip install paker -U
```

From source

```shell
git clone https://github.com/desty2k/paker.git
cd paker
pip install .
```

## Usage

Let's use `paker` to dump `print_lib` module with following directory structure:
```
print_lib/
├── __init__.py
├── capitalize.py
└── decapitalize.py
 ```
`capitalized.py` contains `Print` function and `decapitalized.py` contains `pRINT` function. `__init__.py` file is empty.

### Dumping to JSON
To dump the library to a file use `paker dump` command:
```shell
paker dump print_lib --indent 4
```
Paker will create new file `print_lib.json` in the current directory.
```json
{
    "print_lib": {
        "type": "package",
        "extension": "py",
        "code": "",
        "modules": {
            "capitalize": {
                "type": "module",
                "extension": "py",
                "code": "def Print(text: str, *args, **kwargs):\n    print(text.capitalize(), *args, **kwargs)\n"
            },
            "decapitalize": {
                "type": "module",
                "extension": "py",
                "code": "def pRINT(text: str, *args, **kwargs):\n    print(text[0].lower() + text[1:].upper(), *args, **kwargs)\n"
            }
        }
    }
}
```
### Listing modules
To list all modules in the JSON use `paker list` command:
```shell
paker list print_lib.json
```

Output:
```text
P print_lib
M print_lib.capitalize
M print_lib.decapitalize
```

### Recreating package from JSON
To recreate the package from the JSON use `paker load` command.
```shell
paker load print_lib.json
```
`print_lib` package will be created in the current directory. To set custom output path use `--output` option.

### Importing from `.json`
__Note:__ Importing from original source code has higher priority than importing from JSON. 
Remember to remove module from disk before importing the library with `paker`.

```python
import paker
import logging

logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    with open("print_lib.json", "r") as f:
        with paker.load(f) as loader:
            # print_lib will be available only in this context
            from print_lib.capitalize import Print
            from print_lib.decapitalize import pRINT
            Print("hello world!")
            pRINT("hello world!")
```

This script produces the following output:
```text
DEBUG:jsonimporter:searching for print_lib
INFO:jsonimporter:print_lib has been imported successfully
DEBUG:jsonimporter:searching for print_lib.capitalize
INFO:jsonimporter:print_lib.capitalize has been imported successfully
DEBUG:jsonimporter:searching for print_lib.decapitalize
INFO:jsonimporter:print_lib.decapitalize has been imported successfully

Hello world!
hELLO WORLD!

INFO:jsonimporter:unloaded all modules
```

### Importing from memory

You can also import Python modules directly from memory. 
Libraries can be loaded not only from Python `dict` objects, but also from `str`, `bytes` and `bytearray` objects. 

```python
import paker
import logging

# paker.loads accepts dict, str, bytes and bytearray objects
POW = {"pow": {"type": "module", "extension": "py", "code": "pow = lambda x, y: x**y"}}
SQR = '{"sqr": {"type": "module", "extension": "py", "code": "from pow import pow\\nsqr = lambda x: pow(x, 2)"}}'
TRI = b'{"tri": {"type": "module", "extension": "py", "code": "from pow import pow\\ntri = lambda x: pow(x, 3)"}}'

logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    # you can use nested loaders
    with paker.loads(POW) as pow_loader:
        # pow will be available only in this context
        with paker.loads(SQR) as sqr_loader:
            # sqr will be available only in this context
            from sqr import sqr
            assert sqr(2), 4
            assert sqr(5), 25
            print("6**2 is {}".format(sqr(6)))

        with paker.loads(TRI) as tri_loader:
            # tri will be available only in this context
            from tri import tri
            assert tri(2), 8
            assert tri(5), 125
            print("6**3 is {}".format(tri(6)))
        print("It works!")

```
This script produces the following output:
```text
DEBUG:jsonimporter:searching for sqr
INFO:jsonimporter:sqr has been added successfully
DEBUG:jsonimporter:searching for sqr
DEBUG:jsonimporter:searching for pow
INFO:jsonimporter:pow has been imported successfully
INFO:jsonimporter:sqr has been imported successfully

6**2 is 36

INFO:jsonimporter:sqr has been unloaded successfully
DEBUG:jsonimporter:searching for tri
INFO:jsonimporter:tri has been added successfully
DEBUG:jsonimporter:searching for tri
INFO:jsonimporter:tri has been imported successfully

6**3 is 216

INFO:jsonimporter:tri has been unloaded successfully

It works!

INFO:jsonimporter:unloaded all modules
```


## How it works

When importing modules or packages Python iterates over [importers](https://docs.python.org/3/glossary.html#term-importer) in `sys.meta_path` and calls `find_module` method on each object.
If the importer returns itself, it means that the module can be imported and `None` means that importer did not find searched package.
If any importer has confirmed the ability to import module, Python executes another method on it - `load_module`.
Paker implements its own importer called `jsonimporter`, which instead of searching for modules in directories, looks for them in Python dictionaries

To dump module or package to JSON document, Paker recursively iterates over modules and creates dict with 
code and type of each module and submodules if object is package.
