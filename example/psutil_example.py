"""
This example does not work.
Psutil uses .pyd files in Windows.

FIX?
https://github.com/n1nj4sec/pymemimporter
https://github.com/phuslu/zipextimporter
"""

import paker
import logging

file = "psutil.json"
logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    # install mss using `pip install psutil`
    # serialize module
    with open(file, "w+") as f:
        paker.dump("psutil", f, indent=4)

    # now you can uninstall mss using `pip uninstall psutil -y`
    # load package back from dump file
    with open(file, "r") as f:
        loader = paker.load(f)

    # this will throw error
    import psutil
    print("CPU count is {}".format(psutil.cpu_count()))
