"""
Use this script to dump cv2 to JSON file.
Make sure that opecv-python is installed.
"""

import cv2
import paker

with open("cv2.json", "w+") as f:
    paker.dump(cv2, f)
