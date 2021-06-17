# Paker
Paker is module for serializing and deserializing Python modules and packages. 
It was inspired by [httpimporter](https://github.com/operatorequals/httpimport).

## How it works
Paker dumps entire package structure to JSON dict. 
When loading package back, package is recreated with its submodules and subpackages.


## Usage
Check example directory for more scripts.

```python

import json
import paker
import logging

file = "mss.json"
logging.basicConfig(level=logging.NOTSET)

# install mss using `pip install mss`
# serialize module
serialized = paker.dump("mss")
serialized = json.dumps(serialized, indent=4)
with open(file, "w+") as f:
    f.write(serialized)

# now you can uninstall mss using `pip uninstall mss -y`
# load package back from dump file
with open(file, "r") as f:
    loader = paker.load(json.loads(f.read()))

import mss
print(dir(mss))
with mss.mss() as sct:
    sct.shot()

```

## Bugs

Loading modules from `.pyd` files does not work.
