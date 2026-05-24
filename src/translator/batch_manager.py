import time
from config import BATCH_DEBOUNCE_MS, BATCH_MAX_WAIT_MS, BATCH_MAX_SIZE


class BatchManager:
    """Accumulates (bbox, text) items and flushes batches on timer/size triggers."""

    def __init__(self):
        self._items = []  # list of (bbox, text)
        self._first_arrival = None
        self._last_arrival = None

    def add(self, bbox, text: str):
        self._items.append((bbox, text))
        now = time.monotonic()
        if self._first_arrival is None:
            self._first_arrival = now
        self._last_arrival = now

    def should_flush(self) -> bool:
        if not self._items:
            return False
        if len(self._items) >= BATCH_MAX_SIZE:
            return True
        now = time.monotonic()
        if self._last_arrival and (now - self._last_arrival) * 1000 >= BATCH_DEBOUNCE_MS:
            return True
        if self._first_arrival and (now - self._first_arrival) * 1000 >= BATCH_MAX_WAIT_MS:
            return True
        return False

    def pop_batch(self) -> tuple[list, list]:
        """Returns (bboxes, texts) of the batch."""
        batch = self._items[:BATCH_MAX_SIZE]
        self._items = self._items[len(batch):]
        if not self._items:
            self._first_arrival = None
            self._last_arrival = None
        else:
            self._first_arrival = time.monotonic()
        bboxes = [b for b, _ in batch]
        texts = [t for _, t in batch]
        return bboxes, texts

    def is_empty(self) -> bool:
        return len(self._items) == 0
