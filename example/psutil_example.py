"""
This example does not work.
Psutil uses .pyd files in Windows.

FIX?
https://github.com/n1nj4sec/pymemimporter
https://github.com/phuslu/zipextimporter
"""

import json
import paker
import logging

file = "psutil.json"
logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    # install mss using `pip install psutil`
    # serialize module
    serialized = paker.dump("psutil")
    serialized = json.dumps(serialized, indent=4)
    with open(file, "w+") as f:
        f.write(serialized)

    # now you can uninstall mss using `pip uninstall psutil -y`
    # load package back from dump file
    with open(file, "r") as f:
        loader = paker.load(json.loads(f.read()))

    import psutil
    print(dir(psutil))
    print("CPU count is {}".format(psutil.cpu_count()))
