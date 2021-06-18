import paker
import logging

file = "mss.json"
logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    # install mss using `pip install mss`
    # serialize module

    with open(file, "w+") as f:
        paker.dump("mss", f, indent=4)

    # now you can uninstall mss using `pip uninstall mss -y`
    # load package back from dump file
    with open(file, "r") as f:
        loader = paker.load(f)

    # import mss and take screenshot
    import mss
    with mss.mss() as sct:
        sct.shot()

    # remove loader and clean cache
    loader.unload()

    # this will throw error
    import mss

