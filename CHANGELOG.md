# Changelog

- 0.7.1:
  - Replace `skip_main` with `skip_modules`
  - Add check if module loader type is `SourceFileLoader` in `dump` function
  - Clear module stack in `jsonimporter.unload` function
  - Add info to README about recreating modules with `paker load` CLI command

- 0.7.0:
  - Make all `jsonimporter` instance variables private
  - Remove multiload example

- 0.6.2:
  - Update examples in README.md
  - Fix all modules being unloaded when using nested loaders

- 0.6.1:
  - Disable building wheels when uploading to PyPI

- 0.6.0:
  - Add support for `_memimporter`
  - Replace `exceptions` package with module
  - Update _tempimporter.import_module function to match
    _memimporter.import_module() signature
  - Use `.bat` script to remove `paker` temporary directory
    on `win32` platform

- 0.5.2:
  - Run multi-platform tests
  - Support for Python 3.6 for version test

- 0.5.1:
  - Multiloading: use dict keys as module names when adding modules to jsonimporter
  - use `if obj is not None` instead of `if obj`

- 0.5.0:
  - Import multiple modules from one jsonimporter to reduce memory usage
  - Add multiload example
  - Add docstrings

- 0.4.4:
  - Update example for importing modules from stream
  - Add `-U` flag to PyPI installation instructions

- 0.4.3:
  - Add utils submodule
  - Add extensions compatiblity check
  - Move \_\_version__ variable to paker package

- 0.4.2:
  - Add test for getting paker version
  - Move _tempimporter and jsonimporter to importers package
  - Replace exception module with exceptions package  
  - Fix import errors when freezing paker with Pyinstaller

- 0.4.1:
  - Add module cache for tempimporter
  - Add automatic cleanup for tempimporter
  - Use single temporary directory for importing dynamic libraries  
  - Update logging strings

- 0.4.0:
  - Add support for `.dll`, `.pyd` and `.so` files
  - Add support for `.pyc` files
  - Add dump option `compile_modules` (`-C` in CLI mode)
  - Add cv2 example
  - Add docstrings
  - Update psutil example (importing `.pyd` files works now)
  - Fix remove `.json` file if dump failed

- 0.3.6:
  - Add dill example
  - Add license files for dill and mss examples
  - Add string import example

- 0.3.5:
  - Update readme
  - Fix set utf-8 encoding when opening files in CLI mode
  - Fix --output not working for CLI load
  - Fix skip nested packages when dumping
  - Use readme.md instead of \_\_doc__ from \_\_main__ for PyPI long description

- 0.3.4:
  - Add `paker list` CLI command
  - Add CLI commands descriptions
  - Add stream import example  
  - Add PyPI classifiers and keywords  
  - Do not print \_\_doc__ in paker help
  - Update description in readme
  
- 0.3.3:
  - Unload jsonimporter on \_\_exit__
  - Remove modules from cache when unloading jsonimporter
  - Use utf-8 encoding when for reading files, fixes decode error

- 0.3.2:
  - Add badges in readme
  - Clean the code, fix typos
  - Fix packages not being properly recreated in CLI mode
  - Add long project description for PyPI

- 0.3.1:
  - Add arg parser
  - Add dump/load from CLI
  - Add build and deploy workflows
  - Add tests
  - Fix dumping single modules

- 0.3.0:
  - Use JSON dicts instead of zip files
  - Reduce the code size by about 7 times
  - Fix relative imports when importing from JSON dicts
  - Prepare for creating PyPI release

- 0.2.0:
  - Add setup.py file for pip
  - Use zip files instead of JSON dicts
  - Fix relative imports

- 0.1.0:
  - Create repo
  - Add first working version
  - Add examples
