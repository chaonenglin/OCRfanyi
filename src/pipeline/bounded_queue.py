import queue
import threading


class BoundedQueue:
    """Thread-safe queue that drops oldest items when full."""

    def __init__(self, maxsize=50):
        self._queue = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()

    def put(self, item):
        with self._lock:
            while self._queue.full():
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    break
            self._queue.put(item)

    def get(self, timeout=None):
        return self._queue.get(timeout=timeout)

    def get_nowait(self):
        return self._queue.get_nowait()

    def empty(self):
        with self._lock:
            return self._queue.empty()

    def qsize(self):
        with self._lock:
            return self._queue.qsize()
