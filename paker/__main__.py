import os
import sys
import json
import base64
import marshal
import logging
import argparse

from paker import dump

__version__ = "0.4.2"


def _dump(args):
    """CLI mode. Dump module to JSON formatted document."""
    sys.path.insert(0, os.path.abspath("."))
    mod = args.module[0]
    output_path = args.output if args.output else mod
    indent = args.indent if args.indent else None

    if not output_path.endswith(".json"):
        output_path = output_path + ".json"

    try:
        with open(output_path, "w+") as f:
            dump(mod, f, indent=indent, compile_modules=args.compile)
    except Exception:
        os.remove(output_path)
        raise


def _list(args):
    """CLI mode. List all modules and package in JSON file."""
    path = args.module[0]
    with open(path, "r") as f:
        mod_dict = json.loads(f.read())
    return _recursive_list(mod_dict)


def _recursive_list(mod_dict: dict, parent=""):
    """Recursively print modules."""
    for key, value in mod_dict.items():
        if value["type"] == "package":
            if parent:
                key = parent + "." + key
            print("P {}".format(key))
            _recursive_list(value["modules"], parent=key)
        else:
            if parent:
                key = parent + "." + key
            print("M {}".format(key))


def _load(args):
    """CLI mode. Recreate directory structure from JSON file."""
    path = args.module[0]
    with open(path, "r", encoding="utf-8") as f:
        mod_dict = json.loads(f.read())
    output = os.path.abspath(args.output)
    os.makedirs(output, exist_ok=True)
    os.chdir(output)
    return _recursive_load(mod_dict)


def _recursive_load(mod_dict: dict):
    """Recursively create directories and modules."""
    for key, value in mod_dict.items():
        if value["type"] == "package":
            os.makedirs(key, exist_ok=True)
            old_dir = os.getcwd()
            os.chdir(key)
            _write_file("__init__", value["code"], value["extension"])
            _recursive_load(value["modules"])
            os.chdir(old_dir)
        else:
            _write_file(key, value["code"], value["extension"])


def _write_file(path, code, extension):
    """Create file with source code."""
    if extension == "py":
        with open(path + "." + extension, "w+", encoding="utf-8") as f:
            f.write(code)
    elif extension == "pyc":
        with open(path + "." + extension, "wb+") as f:
            marshal.dump(marshal.loads(base64.b64decode(code)), f)
    elif extension in ("dll", "pyd", "so"):
        with open(path + "." + extension, "wb+") as f:
            f.write(base64.b64decode(code))


def _parser():
    parser = argparse.ArgumentParser(
        prog='paker',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Import Python modules from JSON documents.",
        epilog='See "paker <command> -h" for more information '
               'on a specific command.'
    )

    parser.add_argument('-V', '--version', action='version', version='v{}'.format(__version__),
                        help='print version and exit')

    subparsers = parser.add_subparsers(
        title='Available commands',
        metavar=''
    )

    # find and dump module to JSON
    cparser = subparsers.add_parser(
        'dump',
        aliases=['d'],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help='Dump package to JSON dict',
        description="Dump package to JSON dict"
    )
    cparser.add_argument('module', nargs=1, metavar='MODULE',
                         help='Module name')
    cparser.add_argument('-O', '--output', metavar='PATH',
                         help='Output file name, default is module name')
    cparser.add_argument('-I', '--indent', metavar='INDENT', type=int, default=None,
                         help='Indent in JSON document, default is %(default)s')
    cparser.add_argument('-C', '--compile', action="store_true",
                         help='Compile and optimize code')
    cparser.set_defaults(func=_dump)

    # recreating module tree from JSON
    cparser = subparsers.add_parser(
        'load',
        aliases=['l'],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help='Recreate module from JSON dict',
        description="Recreate module from JSON dict"
    )
    cparser.add_argument('module', nargs=1, metavar='MODULE',
                         help='Path to JSON document with module')
    cparser.add_argument('-O', '--output', default='.', metavar='PATH',
                         help='Output directory path, default is "%(default)s"')
    cparser.set_defaults(func=_load)

    # list modules and packages in json file
    cparser = subparsers.add_parser(
        'list',
        aliases=['ls'],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help='List modules and packages in JSON document',
        description="List modules and packages in JSON document"
    )
    cparser.add_argument('module', nargs=1, metavar='MODULE',
                         help='Path to JSON document with module')
    cparser.set_defaults(func=_list)
    return parser


def main(argv):
    parser = _parser()
    args = parser.parse_args(argv)
    if not hasattr(args, 'func'):
        parser.print_help()
        return

    args.func(args)


def main_entry():
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)-8s %(message)s',
    )
    main(sys.argv[1:])


if __name__ == '__main__':
    main_entry()
