# Paker
Paker is module for serializing and deserializing Python modules and packages. 
It was inspired by [httpimporter](https://github.com/operatorequals/httpimport).
The core of the module is a modified version of the zipimporter that allows 
loading zip files into spooled temporary files.

## How it works
Paker dumps entire package to zip file. When loading package back, importer class 
creates spooled temporary file with zip contents.


## Usage
Check example directory for more scripts.

```python
import io
import paker
import logging

logging.basicConfig(level=logging.NOTSET)

# install mss using `pip install mss`
# serialize and write module to zip file
serialized = paker.dump("mss")

# now you can uninstall mss using `pip uninstall mss -y`
# load package back from dump file
with open("mss.zip", "rb") as f:
    zip_bytes = io.BytesIO(f.read())

with paker.loads(zip_bytes.read()) as loader:
    loader.load_module("mss")
    import mss
    
    print(dir(mss))
    with mss.mss() as sct:
        sct.shot()
```

## Bugs

Loading modules from `.pyd` files does not work.
