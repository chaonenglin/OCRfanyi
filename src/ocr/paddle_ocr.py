import os
import sys
import warnings
import numpy as np
import easyocr
from PIL import Image, ImageFilter, ImageEnhance
from config import OCR_LANG_LIST, OCR_CONFIDENCE_THRESHOLD

_MODEL_DIR = None

def _get_model_dir():
    global _MODEL_DIR
    if _MODEL_DIR is not None:
        return _MODEL_DIR
    if getattr(sys, 'frozen', False):
        _MODEL_DIR = os.path.join(sys._MEIPASS, 'models')
    else:
        _MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'models')
    return _MODEL_DIR


class EasyOCREngine:
    def __init__(self, lang_list=None):
        if lang_list is None:
            lang_list = OCR_LANG_LIST
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.reader = easyocr.Reader(lang_list, gpu=True, verbose=False,
                                         model_storage_directory=_get_model_dir(),
                                         download_enabled=False)

    def _preprocess(self, image: np.ndarray) -> np.ndarray:
        """Enhance image for better OCR: grayscale + sharpen + contrast."""
        pil = Image.fromarray(image[..., :3])  # BGRA → RGB
        pil = pil.convert("L")  # grayscale
        pil = ImageEnhance.Contrast(pil).enhance(1.8)
        pil = pil.filter(ImageFilter.SHARPEN)
        return np.array(pil)

    def detect_and_recognize(self, image: np.ndarray):
        h, w = image.shape[:2]
        mag = max(1.0, min(3.0, 1200 / max(h, w)))  # upscale for small/detailed text
        preprocessed = self._preprocess(image)
        results = self.reader.readtext(preprocessed, text_threshold=0.4,
                                       low_text=0.2, mag_ratio=mag)
        items = []
        for bbox, text, conf in results:
            if conf >= OCR_CONFIDENCE_THRESHOLD and text.strip():
                xs = [p[0] for p in bbox]
                ys = [p[1] for p in bbox]
                x, y = int(min(xs)), int(min(ys))
                w, h = int(max(xs) - x), int(max(ys) - y)
                items.append(((x, y, w, h), text.strip(), conf))

        # Sort in reading order: top-to-bottom, left-to-right
        if items:
            avg_h = sum(h for (_, _, _, h), _, _ in items) / len(items)
            row_tol = max(1, int(avg_h * 0.5))
            items.sort(key=lambda it: (it[0][1] // row_tol, it[0][0]))

        return items
