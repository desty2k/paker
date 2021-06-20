import sys
import logging
import _frozen_importlib_external as _bootstrap_external

_module_type = type(sys)
path_sep = _bootstrap_external.path_sep


class jsonimporter:

    def __init__(self, jsonmod):
        super(jsonimporter, self).__init__()
        self.jsonmod = jsonmod
        self.module_cache = {}
        self.logger = logging.getLogger(self.__class__.__name__)
        sys.meta_path.append(self)

    # Check whether we can satisfy the import of the module named by
    # 'fullname', or whether it could be a portion of a namespace
    # package. Return self if we can load it, a string containing the
    # full path if it's a possible namespace portion, None if we
    # can't load it.
    def find_loader(self, fullname: str, path=None):
        """find_loader(fullname, path=None) -> self, str or None.

        Search for a module specified by 'fullname'. 'fullname' must be the
        fully qualified (dotted) module name. It returns the zipimporter
        instance itself if the module was found, a string containing the
        full path name if it's possibly a portion of a namespace package,
        or None otherwise. The optional 'path' argument is ignored -- it's
        there for compatibility with the importer protocol.
        """
        path = fullname.split(".")
        try:
            jsonmod = self.jsonmod[path[0]]
            for submod in path[1:]:
                jsonmod = jsonmod["modules"][submod]
            return self, []
        except KeyError:
            return None, []

    def find_module(self, fullname, path=None):
        """find_module(fullname, path=None) -> self or None.

        Search for a module specified by 'fullname'. 'fullname' must be the
        fully qualified (dotted) module name. It returns the zipimporter
        instance itself if the module was found, or None if it wasn't.
        The optional 'path' argument is ignored -- it's there for compatibility
        with the importer protocol.
        """
        self.logger.debug("[?] Searching for {}".format(fullname))
        return self.find_loader(fullname, path)[0]

    # Load and return the module named by 'fullname'.
    def load_module(self, fullname):
        """load_module(fullname) -> module.

        Load the module specified by 'fullname'. 'fullname' must be the
        fully qualified (dotted) module name. It returns the imported
        module, or raises ZipImportError if it wasn't found.
        """
        if fullname in self.module_cache:
            self.logger.info("[=] Loading previously imported module {}".format(fullname))
            return self.module_cache[fullname]

        path = fullname.split(".")
        jsonmod = self.jsonmod[path[0]]
        for submod in path[1:]:
            jsonmod = jsonmod["modules"][submod]

        mod = sys.modules.get(fullname)
        if mod is None or not isinstance(mod, _module_type):
            mod = _module_type(fullname)
            sys.modules[fullname] = mod
        mod.__loader__ = self

        try:
            if jsonmod["type"] == "package":
                mod.__path__ = [fullname.replace(".", path_sep)]

            if not hasattr(mod, '__builtins__'):
                mod.__builtins__ = __builtins__
            exec(jsonmod["code"], mod.__dict__)
        except Exception:
            del sys.modules[fullname]
            raise

        try:
            mod = sys.modules[fullname]
        except KeyError:
            raise ImportError(f'Loaded module {fullname!r} not found in sys.modules')
        self.logger.info("[+] {} has been loaded successfully".format(mod.__name__))
        self.module_cache[fullname] = mod
        return mod

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.unload()

    def unload_module(self, module):
        if isinstance(module, _module_type):
            module = module.__name__
        if module in self.module_cache:
            del self.module_cache[module]
        if module in sys.modules:
            del sys.modules[module]

    def unload(self):
        if self in sys.meta_path:
            sys.meta_path.remove(self)
        for module_name in list(self.module_cache.keys()):
            del self.module_cache[module_name]
            if module_name in sys.modules:
                del sys.modules[module_name]
