import sys
import paker
import logging

from paker.utils import get_jsonimporter_from_meta_path


if __name__ == '__main__':
    logging.basicConfig(level=logging.NOTSET)
    print("Current meta path: {}".format(sys.meta_path))

    first_module = paker.load(open("./packages/firstpackage.json", "r"))
    print("Meta path after importing first package: {}".format(sys.meta_path))

    second_module = paker.load(open("./packages/secondpackage.json", "r"))
    print("Meta path after importing second package: {}".format(sys.meta_path))

    print("jsonimporter's dict: {}".format(get_jsonimporter_from_meta_path().jsonmod))

    from firstpackage import PRINT
    from secondpackage import Print
    PRINT("printing from first package")
    Print("printing from second package")
