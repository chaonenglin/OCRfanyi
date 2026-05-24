import threading
import time
import asyncio
from collections import OrderedDict

from config import CAPTURE_INTERVAL_MS, ENTRY_TTL_SECONDS, TRANSLATION_CACHE_SIZE, OVERLAY_FPS
from src.capture.screen_capture import ScreenCapture
from src.ocr.ocr_pool import OCRPool
from src.translator.deepseek_client import DeepSeekClient
from src.translator.batch_manager import BatchManager
from src.overlay.overlay_window import OverlayWindow
from src.overlay.text_renderer import TextRenderer
from src.overlay.selection_window import SelectionWindow
from src.pipeline.bounded_queue import BoundedQueue


class Coordinator:
    def __init__(self):
        self.running = False
        self.enabled = False
        self.threads = []
        self._selected_region = None  # (x, y, w, h) or None

        self.capture = ScreenCapture()
        self.ocr_pool = OCRPool()
        self.translator_client = DeepSeekClient()
        self.renderer = TextRenderer(self.capture.width, self.capture.height)
        self.overlay = OverlayWindow(self.capture.width, self.capture.height)

        self.ocr_queue = BoundedQueue(maxsize=50)
        self.translate_queue = BoundedQueue(maxsize=200)
        self.result_queue = BoundedQueue(maxsize=1)

        self._entries = {}  # bbox_tuple -> (bbox, translated_text, timestamp)
        self._entries_lock = threading.Lock()

        self._trans_cache = OrderedDict()
        self._trans_cache_lock = threading.Lock()

    def start(self):
        if self.running:
            return
        self.running = True

        capture_t = threading.Thread(target=self._capture_loop, daemon=True, name="capture")
        ocr_t = threading.Thread(target=self._ocr_loop, daemon=True, name="ocr")
        translate_t = threading.Thread(target=self._translate_loop, daemon=True, name="translate")
        render_t = threading.Thread(target=self._render_loop, daemon=True, name="render")

        self.threads = [capture_t, ocr_t, translate_t, render_t]
        for t in self.threads:
            t.start()
        print("[Coordinator] Ready. Press Ctrl+Shift+R to select a region.")

    def stop(self):
        self.running = False
        self.enabled = False
        for t in self.threads:
            t.join(timeout=3)
        asyncio.run(self.translator_client.close())
        self.overlay.destroy()
        print("[Coordinator] Stopped")

    def toggle(self):
        if self._selected_region is None:
            print("[Coordinator] No region selected. Use Ctrl+Shift+R first.")
            return
        if self.enabled:
            self.enabled = False
            self.overlay.hide()
            print("[Coordinator] Paused")
        else:
            self.enabled = True
            self.overlay.show()
            print("[Coordinator] Resumed")

    def region_translate(self):
        """Ctrl+Shift+R: user drags a rectangle, then continuously monitor that region."""
        was_enabled = self.enabled
        self.enabled = False
        self.overlay.hide()

        try:
            win = SelectionWindow(self.capture.width, self.capture.height)
            rect = win.run()
            win.destroy()

            if rect is None or rect[2] < 10 or rect[3] < 10:
                print("[Region] Selection cancelled or too small")
                self.enabled = was_enabled
                if was_enabled:
                    self.overlay.show()
                return

            x, y, w, h = rect
            print(f"[Region] Monitoring: ({x},{y}) {w}x{h}")
            self._selected_region = (x, y, w, h)
            self.enabled = True
            self.overlay.show()
        except Exception as e:
            print(f"[Region] Error: {e}")
            self.enabled = was_enabled

    # ---- Capture Thread ----
    def _capture_loop(self):
        import hashlib
        prev_hash = None
        while self.running:
            if not self.enabled or self._selected_region is None:
                time.sleep(0.1)
                continue

            x, y, w, h = self._selected_region
            try:
                img = self.capture.capture_region(x, y, w, h)
                h = hashlib.md5(img.tobytes()).digest()
                if h == prev_hash:
                    time.sleep(CAPTURE_INTERVAL_MS / 1000.0)
                    continue
                prev_hash = h
                self.ocr_queue.put((x, y, w, h, img))
            except Exception as e:
                print(f"[Capture] Error: {e}")

            time.sleep(CAPTURE_INTERVAL_MS / 1000.0)

    # ---- OCR Thread ----
    def _ocr_loop(self):
        while self.running:
            if not self.enabled:
                time.sleep(0.05)
                continue

            try:
                cx, cy, cw, ch, cell_img = self.ocr_queue.get(timeout=0.5)
            except Exception:
                continue

            try:
                results = self.ocr_pool.process(cell_img)
                for (bx, by, bw, bh), text, conf in results:
                    abs_bbox = (cx + bx, cy + by, bw, bh)
                    # Check translation cache first
                    with self._trans_cache_lock:
                        if text in self._trans_cache:
                            self._trans_cache.move_to_end(text)
                            translated = self._trans_cache[text]
                            self.result_queue.put((abs_bbox, translated))
                            continue
                    self.translate_queue.put((abs_bbox, text))
            except Exception as e:
                print(f"[OCR] Error: {e}")

    # ---- Translate Thread ----
    def _translate_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._translate_async())
        finally:
            loop.close()

    async def _translate_async(self):
        batch_mgr = BatchManager()

        async def flush():
            bboxes, texts = batch_mgr.pop_batch()
            if not texts:
                return

            # Deduplicate texts for API call while preserving order
            unique_texts = []
            index_map = []  # maps unique_idx -> original_idx
            for i, t in enumerate(texts):
                with self._trans_cache_lock:
                    if t in self._trans_cache:
                        self._trans_cache.move_to_end(t)
                        self.result_queue.put((bboxes[i], self._trans_cache[t]))
                    else:
                        unique_texts.append(t)
                        index_map.append(i)

            if not unique_texts:
                return

            try:
                translations = await self.translator_client.translate_batch(unique_texts)
            except Exception as e:
                print(f"[Translate] API error: {e}")
                return

            # Map back and cache
            with self._trans_cache_lock:
                for ti, ui in enumerate(index_map):
                    trans = translations[ti]
                    bbox = bboxes[ui]
                    # Cache
                    if len(self._trans_cache) >= TRANSLATION_CACHE_SIZE:
                        self._trans_cache.popitem(last=False)
                    self._trans_cache[texts[ui]] = trans
                    self.result_queue.put((bbox, trans))

        while self.running:
            if not self.enabled:
                await asyncio.sleep(0.1)
                continue

            try:
                bbox, text = self.translate_queue.get(timeout=0.3)
                batch_mgr.add(bbox, text)
            except Exception:
                pass

            if batch_mgr.should_flush():
                await flush()

        # Final flush
        if not batch_mgr.is_empty():
            await flush()

    # ---- Render Thread ----
    def _render_loop(self):
        interval = 1.0 / OVERLAY_FPS
        while self.running:
            if not self.enabled:
                time.sleep(0.1)
                continue

            # Drain result queue
            try:
                while True:
                    bbox, trans = self.result_queue.get_nowait()
                    with self._entries_lock:
                        self._entries[bbox] = (bbox, trans, time.time())
            except Exception:
                pass

            # Expire old entries
            now = time.time()
            with self._entries_lock:
                expired = [k for k, v in self._entries.items()
                          if now - v[2] > ENTRY_TTL_SECONDS]
                for k in expired:
                    del self._entries[k]
                entries = list(self._entries.values())

            if entries:
                entry_list = [(bbox, text) for bbox, text, _ in entries]
                bitmap = self.renderer.render(entry_list)
                try:
                    self.overlay.update(bitmap)
                except Exception as e:
                    print(f"[Render] Overlay error: {e}")
            else:
                try:
                    self.overlay.hide()
                except Exception:
                    pass

            time.sleep(interval)
