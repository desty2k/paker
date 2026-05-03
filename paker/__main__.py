import os
import sys
import json
import base64
import marshal
import logging
import argparse

from paker import dump, __version__


def _dump(args):
    """CLI: Dump module to JSON formatted document."""
    sys.path.insert(0, os.path.abspath("."))
    mod = args.module[0]
    output_path = args.output if args.output else mod

    if not output_path.endswith(".json"):
        output_path = output_path + ".json"

    try:
        with open(output_path, "w+") as f:
            dump(mod, f, indent=args.indent)
    except Exception:
        if os.path.exists(output_path):
            os.remove(output_path)
        raise


def _list(args):
    """CLI: List all modules and packages in JSON file."""
    path = args.module[0]
    with open(path, "r") as f:
        mod_dict = json.loads(f.read())
    _recursive_list(mod_dict)


def _recursive_list(mod_dict: dict, parent=""):
    for key, value in mod_dict.items():
        full = f"{parent}.{key}" if parent else key
        if value["type"] == "package":
            print(f"P {full}")
            _recursive_list(value["modules"], parent=full)
        else:
            print(f"M {full}")


def _load(args):
    """CLI: Recreate directory structure from JSON file."""
    path = args.module[0]
    with open(path, "r", encoding="utf-8") as f:
        mod_dict = json.loads(f.read())
    output = os.path.abspath(args.output)
    os.makedirs(output, exist_ok=True)
    _recursive_load(mod_dict, output)


def _recursive_load(mod_dict: dict, base_dir: str):
    for key, value in mod_dict.items():
        if value["type"] == "package":
            pkg_dir = os.path.join(base_dir, key)
            os.makedirs(pkg_dir, exist_ok=True)
            _write_file(os.path.join(pkg_dir, "__init__"), value["code"], value["extension"])
            _recursive_load(value["modules"], pkg_dir)
        else:
            _write_file(os.path.join(base_dir, key), value["code"], value["extension"])


def _write_file(path: str, code: str, extension: str):
    if extension == "py":
        with open(f"{path}.{extension}", "w+", encoding="utf-8") as f:
            f.write(code)
    elif extension == "pyc":
        with open(f"{path}.{extension}", "wb+") as f:
            marshal.dump(marshal.loads(base64.b64decode(code)), f)
    elif extension in ("dll", "pyd", "so"):
        with open(f"{path}.{extension}", "wb+") as f:
            f.write(base64.b64decode(code))


def _parser():
    parser = argparse.ArgumentParser(
        prog="paker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Import Python modules from JSON documents.",
        epilog='See "paker <command> -h" for more information on a specific command.',
    )
    parser.add_argument(
        "-V", "--version", action="version",
        version=f"v{__version__}",
    )

    subparsers = parser.add_subparsers(title="Available commands", metavar="")

    # dump
    p = subparsers.add_parser(
        "dump", aliases=["d"],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Dump package to JSON dict",
    )
    p.add_argument("module", nargs=1, metavar="MODULE")
    p.add_argument("-O", "--output", metavar="PATH")
    p.add_argument("-I", "--indent", metavar="INDENT", type=int, default=None)
    p.set_defaults(func=_dump)

    # load
    p = subparsers.add_parser(
        "load", aliases=["l"],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Recreate module from JSON dict",
    )
    p.add_argument("module", nargs=1, metavar="MODULE")
    p.add_argument("-O", "--output", default=".", metavar="PATH")
    p.set_defaults(func=_load)

    # list
    p = subparsers.add_parser(
        "list", aliases=["ls"],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="List modules and packages in JSON document",
    )
    p.add_argument("module", nargs=1, metavar="MODULE")
    p.set_defaults(func=_list)

    return parser


def main(argv):
    parser = _parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return
    args.func(args)


def main_entry():
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
    main(sys.argv[1:])


if __name__ == "__main__":
    main_entry()
