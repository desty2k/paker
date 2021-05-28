"""
This example does not work on Windows.
Psutil uses .pyd files.

FIX?
https://github.com/n1nj4sec/pymemimporter
https://github.com/phuslu/zipextimporter
"""

import io
import paker
import logging


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    # serialize and write module to file
    serialized = paker.dump("psutil")

    # now you can uninstall mss using `pip uninstall psutil -y`
    # load package back from dump file
    with open("psutil.zip", "rb") as f:
        zip_bytes = io.BytesIO(f.read())

    with paker.loads(zip_bytes.read()) as loader:
        loader.load_module("psutil")
        import psutil

        print(dir(psutil))
        print("CPU count is {}".format(psutil.cpu_count()))
