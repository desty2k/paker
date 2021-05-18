import sys
import json
import types
import typing
import pkgutil
import importlib


class Paker:
    def __init__(self):
        super(Paker, self).__init__()

    def dump(self, module: typing.Union[str, types.ModuleType], skip_main: bool = True,
             keep_paths: bool = True):
        """Dump Python module or package. Module argument can be module or module name.
        If skip_main is set, __main__ modules will be skipped.

        Args:
            module (Union[str, ModuleType]): module or its name.
            skip_main (bool): skip __main__ files.
            keep_paths (bool): keep original __file__ paths
        """
        if type(module) is str:
            module = importlib.import_module(module)

        modules = {"type": "package",
                   "name": module.__name__,
                   "package_name": module.__package__,
                   "path": "",
                   "modules": [],
                   "source": ""}

        # serialize module, it is usually __init__ script
        with open(module.__file__, "rb") as f:
            try:
                modules["source"] = f.read().decode()
            except Exception as e:
                print("Dump error in package <{} - {}> {}".format(module.__name__, module.__file__, e))
                raise e

            if keep_paths:
                modules["path"] = module.__file__
            else:
                modules["path"] = "packerdump"
            # modules["package_name"] = module.__package__

        # serialize submodules and subpackages
        for loader, module_name, is_pkg in pkgutil.walk_packages(module.__path__):
            if module_name == "__main__" and skip_main:
                continue

            full_name = module.__name__ + '.' + module_name
            if is_pkg:
                # recurse to every submodule
                modules["modules"].append(self.dump(full_name, skip_main=skip_main, keep_paths=keep_paths))
            else:
                # load module
                try:
                    full_module = importlib.import_module(full_name)
                except ImportError:
                    continue

                if full_module.__file__.endswith(".pyd"):
                    continue

                with open(full_module.__file__, "rb") as f:
                    module_src = f.read()

                if keep_paths:
                    module_file = module.__file__
                else:
                    module_file = "packerdump"
                modules["modules"].append({"type": "module",
                                           "name": module_name,
                                           "package_name": "",
                                           "path": module_file,
                                           "source": module_src.decode()})
        return modules

    def load(self, module: typing.Union[str, dict], top_level=True):
        """Load Python module.

        Args:
            module (Union[str, dict]): serialized module.
            top_level (bool): if module should be imported to sys.modules.
        """

        if type(module) is str:
            module = json.loads(module)

        mod_name = module.get("name")
        mod_type = module.get("type")
        mod_source = module.get("source")

        mod = types.ModuleType(mod_name)
        mod.__file__ = module.get("path")
        mod.__loader__ = self
        mod.__package__ = module.get("package_name")
        mod.__path__ = ['/'.join(mod.__file__.split('/')[:-1]) + '/']

        if top_level:
            sys.modules[mod_name] = mod

        if mod_type == "package":
            for sub_module in module.get("modules"):
                loaded = self.load(sub_module, top_level=False)
                setattr(mod, loaded.__name__, loaded)

        try:
            exec(mod_source, mod.__dict__)
            print("[+] Import {} success".format(mod_name))
        except Exception as e:
            print("[!] Import {} failed".format(mod_name))

        return mod
