import json
import paker
import logging

file = "mss.json"
logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    # install mss using `pip install mss`
    # serialize module
    # serialized = paker.dump("mss")
    # serialized = json.dumps(serialized, indent=4)
    # with open(file, "w+") as f:
    #     f.write(serialized)

    # now you can uninstall mss using `pip uninstall mss -y`
    # load package back from dump file
    with open(file, "r") as f:
        loader = paker.load(json.loads(f.read()))

    import mss
    print(dir(mss))
    with mss.mss() as sct:
        sct.shot()
