import paker
import logging

logging.basicConfig(level=logging.NOTSET)


if __name__ == '__main__':
    with open("print_lib.json", "r") as f:
        with paker.load(f) as loader:
            # print_lib will be available only in this context
            from print_lib.capitalize import Print
            from print_lib.decapitalize import pRINT
            Print("Hello world!")
            pRINT("Hello world!")
