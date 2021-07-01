"""
Example for importing mss from JSON file using paker.
If you have mss installed you should remove it by using `pip uninstall mss -y` command.
"""


import paker
import logging

file = "mss.json"
logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    with open(file, "r") as f:
        loader = paker.load(f)

    # import mss and take screenshot
    import mss
    with mss.mss() as sct:
        sct.shot()

    # remove loader and clean cache
    loader.unload()

    # this will throw error
    try:
        import mss
    except ImportError:
        print("mss unloaded successfully!")
