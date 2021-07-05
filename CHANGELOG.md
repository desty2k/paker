# Changelog

- 0.4.0:
  - Add support for `.dll`, `.pyd` and `.so` files
  - Add support for `.pyc` files
  - Add dump option `compile_modules` (`-C` in CLI mode)
  - Add cv2 example
  - Add docstrings
  - Update psutil example (it works now)
  - Fix remove `.json` file if dump failed

- 0.3.6:
  - Add dill example
  - Add license files for dill and mss (examples)
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
