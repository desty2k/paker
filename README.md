# Paker
Paker is module for serializing and deserializing Python modules and packages. 
It was inspired by [httpimporter](https://github.com/operatorequals/httpimport).
The main difference is that Paker works recursively, so it is possible to serialize entire package
and transfer it to other devices.

## How it works
Paker dumps entire package structure to JSON dict. When loading package back, 
every module is recursively recreated with its submodules and subpackages. 

## Usage
Check example directory for more scripts.

```python
import json
import paker

p = paker.Paker()

# serialize and write module to file
serialized = p.dump("my_own_package", keep_paths=False)
with open("my_own_package.dump", "w+") as f:
    f.write(json.dumps(serialized, indent=4))

# now you can delete package directory
# and load package back from dump file
with open("my_own_package.dump", "r") as f:
    dumped = f.read()

# load() returns loaded module
deserialized = p.load(dumped)
print("After loading: {}".__format__(deserialized))
print(deserialized.module_a.SomeClassFromModuleA.attr1)

# you can also import it
import my_own_package
print("After importing: {}".format(my_own_package))
```

## Bugs

Relative imports does not work. I don't know if it is possible to fix this, 
because Paker does not create directory tree for serialized packages.
