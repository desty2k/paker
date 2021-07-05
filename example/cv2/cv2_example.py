"""
Use `python cv_dump.py` to create json file.
Uninstall opencv-python before running this script.
"""

import paker
import logging
import numpy as np

file = "cv2.json"
logging.basicConfig(level=logging.NOTSET)

if __name__ == '__main__':
    # read psutil code from json and load it using paker
    with open(file, "r") as f:
        with paker.load(f) as loader:
            import cv2

            black = np.zeros([200, 250, 1], dtype="uint8")
            cv2.imshow("Black", black)

            cv2.waitKey(0)
            cv2.destroyAllWindows()
