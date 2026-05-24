import mss
import numpy as np


class ScreenCapture:
    def __init__(self):
        self.sct = mss.mss()
        self.monitor = self.sct.monitors[1]

    def capture_full(self) -> np.ndarray:
        img = self.sct.grab(self.monitor)
        return np.array(img)

    def capture_region(self, x, y, w, h) -> np.ndarray:
        region = {"left": self.left + x, "top": self.top + y,
                  "width": w, "height": h}
        img = self.sct.grab(region)
        return np.array(img)

    @property
    def width(self):
        return self.monitor["width"]

    @property
    def height(self):
        return self.monitor["height"]

    @property
    def left(self):
        return self.monitor["left"]

    @property
    def top(self):
        return self.monitor["top"]
