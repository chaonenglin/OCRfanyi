import warnings
import numpy as np
import easyocr
from config import OCR_LANG_LIST, OCR_CONFIDENCE_THRESHOLD


class EasyOCREngine:
    def __init__(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.reader = easyocr.Reader(OCR_LANG_LIST, gpu=True, verbose=False)

    def detect_and_recognize(self, image: np.ndarray):
        results = self.reader.readtext(image)
        items = []
        for bbox, text, conf in results:
            if conf >= OCR_CONFIDENCE_THRESHOLD and text.strip():
                # bbox is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                x, y = int(min(xs)), int(min(ys))
                w, h = int(max(xs) - x), int(max(ys) - y)
                items.append(((x, y, w, h), text.strip(), conf))
        return items
