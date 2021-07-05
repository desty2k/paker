"""
This example is for Windows platform only.
Some platform-dependent psutil submodules have been removed before dumping.
"""

import os
import paker
import logging

file = "psutil.json"
logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    # make sure psutil is not installed
    os.system("pip uninstall psutil -y")

    # read psutil code from json and load it using paker
    with open(file, "r") as f:
        with paker.load(f) as loader:
            import psutil
            print("CPU count is {}".format(psutil.cpu_count()))
