import queue
from .paddle_ocr import EasyOCREngine
from config import OCR_WORKERS


class OCRPool:
    def __init__(self):
        self._pool = queue.Queue(maxsize=OCR_WORKERS)
        for _ in range(OCR_WORKERS):
            self._pool.put(EasyOCREngine())

    def process(self, image):
        engine = self._pool.get()
        try:
            return engine.detect_and_recognize(image)
        finally:
            self._pool.put(engine)
