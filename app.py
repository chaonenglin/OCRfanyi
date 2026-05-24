"""OCRfanyi Control Panel — tkinter UI for region-select translation.

Click "框选翻译区域" → select a screen region → OCR + translate → results
shown in the log panel AND overlaid on screen. Toggle "自动刷新" to keep
monitoring the same region continuously.
"""

import os
import time
import hashlib
import asyncio
import tkinter as tk
from tkinter import ttk, messagebox
from concurrent.futures import ThreadPoolExecutor

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from src.capture.screen_capture import ScreenCapture
from src.ocr.paddle_ocr import EasyOCREngine
from src.overlay.selection_window import SelectionWindow
from src.overlay.overlay_window import OverlayWindow
from src.overlay.text_renderer import TextRenderer

SYSTEM_PROMPT = """You are a precise English-to-Chinese translator.
- Translate each line of English text to Simplified Chinese.
- Return ONLY the Chinese translation, one line per input line.
- Input lines are separated by "|||". Output translations separated by "|||" in the exact same order.
- Do NOT add explanations, notes, or any extra text.
- If a line is not English or cannot be translated, keep it unchanged."""


def _run_translate(texts, api_key, base_url):
    """Run in worker thread: one-shot asyncio translate call."""
    async def _do():
        import aiohttp
        joined = "|||".join(texts)
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": DEEPSEEK_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": joined},
            ],
            "temperature": 0.0,
            "max_tokens": 2000,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
        if "choices" not in data:
            raise RuntimeError(f"DeepSeek API error: {data}")
        content = data["choices"][0]["message"]["content"].strip()
        translations = [t.strip() for t in content.split("|||")]
        while len(translations) < len(texts):
            translations.append(texts[len(translations)])
        return translations[:len(texts)]

    return asyncio.run(_do())


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("OCRfanyi — 截图翻译")
        self.root.geometry("460x560")
        self.root.resizable(True, True)
        self.root.minsize(360, 400)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Log unhandled tkinter errors
        self._log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "error.log")
        def _report_exc(exc_type, exc_val, exc_tb):
            import traceback
            msg = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            messagebox.showerror("内部错误", msg[-500:])
        self.root.report_callback_exception = _report_exc

        self.capture = None
        self.ocr_engine = None
        self.renderer = None
        self.overlay = None
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._selecting = False

        # Auto-refresh state
        self._auto_region = None  # (x, y, w, h) — set after first selection
        self._auto_active = False
        self._auto_job_id = None
        self._auto_count = 0

        self._build_ui()
        self.root.after(100, self._init_engines)

    def _build_ui(self):
        # --- API Key row ---
        api_frame = ttk.Frame(self.root)
        api_frame.pack(fill=tk.X, padx=10, pady=(10, 4))
        ttk.Label(api_frame, text="API Key:").pack(side=tk.LEFT)
        self.api_key_var = tk.StringVar(value=DEEPSEEK_API_KEY)
        self.api_entry = ttk.Entry(api_frame, textvariable=self.api_key_var, show="*", width=36)
        self.api_entry.pack(side=tk.LEFT, padx=(6, 0))
        self._show_btn = ttk.Button(api_frame, text="显示", width=4,
                                     command=self._toggle_key)
        self._show_btn.pack(side=tk.LEFT, padx=(2, 0))

        # --- Control row (select + auto) ---
        ctrl_frame = ttk.Frame(self.root)
        ctrl_frame.pack(fill=tk.X, padx=10, pady=(8, 2))

        self.select_btn = ttk.Button(ctrl_frame, text="框选翻译区域",
                                      command=self._on_select, state="disabled")
        self.select_btn.pack(fill=tk.X, ipady=8)

        auto_frame = ttk.Frame(self.root)
        auto_frame.pack(fill=tk.X, padx=10, pady=(0, 6))

        self.auto_var = tk.BooleanVar(value=False)
        self.auto_cb = ttk.Checkbutton(auto_frame, text="自动刷新",
                                        variable=self.auto_var,
                                        command=self._on_auto_toggle,
                                        state="disabled")
        self.auto_cb.pack(side=tk.LEFT)

        ttk.Label(auto_frame, text="  间隔:").pack(side=tk.LEFT)
        self.interval_var = tk.StringVar(value="500 ms")
        self.interval_cb = ttk.Combobox(auto_frame, textvariable=self.interval_var,
                                         values=["300 ms", "500 ms", "800 ms", "1 s", "2 s"],
                                         width=8, state="readonly")
        self.interval_cb.pack(side=tk.LEFT, padx=(4, 0))

        self.auto_count_var = tk.StringVar(value="")
        ttk.Label(auto_frame, textvariable=self.auto_count_var).pack(side=tk.RIGHT)

        # --- Log area ---
        log_frame = ttk.LabelFrame(self.root, text=" 翻译记录 ", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 4))

        columns = ("source", "target")
        self.tree = ttk.Treeview(log_frame, columns=columns, show="headings",
                                  height=14, selectmode="extended")
        self.tree.heading("source", text="原文")
        self.tree.heading("target", text="翻译")
        self.tree.column("source", width=200, minwidth=80)
        self.tree.column("target", width=200, minwidth=80)

        scrollbar = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # --- Bottom bar ---
        bottom = ttk.Frame(self.root)
        bottom.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.status_var = tk.StringVar(value="正在加载 OCR 模型，请稍候...")
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)
        ttk.Button(bottom, text="清空记录", command=self._clear_log).pack(side=tk.RIGHT)

    def _toggle_key(self):
        cur = self.api_entry.cget("show")
        self.api_entry.configure(show="" if cur == "*" else "*")
        self._show_btn.configure(text="隐藏" if cur == "*" else "显示")

    def _init_engines(self):
        try:
            self.capture = ScreenCapture()
            self.renderer = TextRenderer(self.capture.width, self.capture.height)
            self.overlay = OverlayWindow(self.capture.width, self.capture.height)
            self.root.update()

            self.ocr_engine = EasyOCREngine()
            self.status_var.set("就绪 — 点击上方按钮框选翻译区域")
            self.select_btn.configure(state="normal")
        except Exception as e:
            self.status_var.set(f"初始化失败: {e}")
            messagebox.showerror("初始化错误", str(e))

    def _parse_interval_ms(self):
        val = self.interval_var.get()
        # Extract leading number; handles "500 ms", "1 s", "800ms", etc.
        num = ""
        for ch in val.strip():
            if ch.isdigit() or ch == ".":
                num += ch
            elif num:
                break
        ms = float(num)
        if "s" in val and "ms" not in val:
            ms *= 1000
        return int(ms)

    def _on_auto_toggle(self):
        if self.auto_var.get():
            self._start_auto()
        else:
            self._stop_auto()

    def _start_auto(self):
        if self._auto_region is None or self._auto_active:
            return
        self._auto_active = True
        self._auto_count = 0
        self.select_btn.configure(state="disabled")
        self.status_var.set("自动刷新中...")
        self._schedule_auto()

    def _stop_auto(self):
        self._auto_active = False
        if self._auto_job_id:
            self.root.after_cancel(self._auto_job_id)
            self._auto_job_id = None
        self.select_btn.configure(state="normal")
        if self.overlay:
            self.overlay.hide()
        self.auto_count_var.set("")
        self.status_var.set("自动刷新已停止")

    def _schedule_auto(self):
        if not self._auto_active:
            return
        interval = self._parse_interval_ms()
        self._auto_job_id = self.root.after(interval, self._auto_tick)

    def _auto_tick(self):
        """One tick: capture → hash check → OCR → (async translate)."""
        if not self._auto_active or self._auto_region is None:
            return

        # Always schedule next tick first (so interval is consistent)
        self._schedule_auto()

        try:
            x, y, w, h = self._auto_region
            api_key = self.api_key_var.get().strip()

            # Capture
            img = self.capture.capture_region(x, y, w, h)

            # Skip if unchanged
            hh = hashlib.md5(img.tobytes()).digest()
            if self._last_auto_hash is not None and hh == self._last_auto_hash:
                return
            self._last_auto_hash = hh

            # OCR
            t0 = time.time()
            results = self.ocr_engine.detect_and_recognize(img)
            ocr_ms = (time.time() - t0) * 1000

            if not results:
                self.status_var.set(f"#{self._auto_count} OCR ({ocr_ms:.0f}ms) — 无变化")
                return

            texts = []
            entries = []
            for (bx, by, bw, bh), text, conf in results:
                abs_bbox = (x + bx, y + by, bw, bh)
                entries.append((abs_bbox, text))
                texts.append(text)

            self._auto_count += 1
            self.status_var.set(f"#{self._auto_count} OCR {ocr_ms:.0f}ms — {len(texts)} 段文字，翻译中...")
            self.auto_count_var.set(f"已刷新 {self._auto_count} 次")

            # Translate in worker thread, result handled via callback
            self._executor.submit(_run_translate, texts, api_key, DEEPSEEK_BASE_URL
                ).add_done_callback(
                    lambda fut, e=entries, st=time.time():
                        self.root.after(0, self._on_auto_translate_done, fut, e, st))

        except Exception as e:
            self.status_var.set(f"自动刷新错误: {e}")

    def _on_auto_translate_done(self, future, entries, t_start):
        """Called on main thread when worker thread finishes translation."""
        if not self._auto_active:
            return

        try:
            translations = future.result(timeout=5)
        except Exception as e:
            self.status_var.set(f"翻译失败: {e}")
            return

        api_ms = (time.time() - t_start) * 1000

        entry_list = [(b, trans) for (b, _), trans in zip(entries, translations)]
        bitmap = self.renderer.render(entry_list)
        self.overlay.update(bitmap)
        self.overlay.show()

        for item in self.tree.get_children():
            self.tree.delete(item)
        for (_, orig), trans in zip(entries, translations):
            self.tree.insert("", 0, values=(orig, trans))

        self.status_var.set(
            f"#{self._auto_count} OK — {len(translations)} 段 | API {api_ms:.0f}ms")

    def _on_select(self):
        if self._selecting:
            return
        self._selecting = True

        # Stop auto while manually selecting
        was_auto = self._auto_active
        if was_auto:
            self._stop_auto()
            self.auto_var.set(False)

        self.select_btn.configure(state="disabled")

        try:
            api_key = self.api_key_var.get().strip()
            if not api_key:
                messagebox.showwarning("未设置 API Key", "请先输入 DeepSeek API Key")
                return
            os.environ["DEEPSEEK_API_KEY"] = api_key

            self.status_var.set("请在屏幕上拖动鼠标框选翻译区域...")
            if self.overlay:
                self.overlay.hide()

            self.root.withdraw()
            self.root.update_idletasks()
            time.sleep(0.15)

            try:
                # --- Selection ---
                win = SelectionWindow(self.capture.width, self.capture.height)
                rect = win.run()
                win.destroy()

                if rect is None or rect[2] < 10 or rect[3] < 10:
                    self.status_var.set("已取消")
                    return

                x, y, w, h = rect
                self._auto_region = (x, y, w, h)  # save for auto refresh
                self._last_auto_hash = None

                self.status_var.set(f"选中 ({x},{y}) {w}x{h}，正在 OCR...")
                self.root.update()

                # --- OCR ---
                t0 = time.time()
                img = self.capture.capture_region(x, y, w, h)
                results = self.ocr_engine.detect_and_recognize(img)
                ocr_ms = (time.time() - t0) * 1000

                if not results:
                    self.status_var.set(f"OCR 完成 ({ocr_ms:.0f}ms)，未检测到文字")
                    return

                texts = []
                entries = []
                for (bx, by, bw, bh), text, conf in results:
                    abs_bbox = (x + bx, y + by, bw, bh)
                    entries.append((abs_bbox, text))
                    texts.append(text)

                self.status_var.set(f"检测到 {len(texts)} 段文字，正在翻译...")
                self.root.update()

                # --- Translate (worker thread) ---
                t1 = time.time()
                future = self._executor.submit(
                    _run_translate, texts, api_key, DEEPSEEK_BASE_URL)
                translations = future.result(timeout=30)
                api_ms = (time.time() - t1) * 1000

                # --- Display ---
                entry_list = [(b, trans) for (b, _), trans in zip(entries, translations)]
                bitmap = self.renderer.render(entry_list)
                self.overlay.update(bitmap)
                self.overlay.show()

                for item in self.tree.get_children():
                    self.tree.delete(item)
                for (_, orig), trans in zip(entries, translations):
                    self.tree.insert("", 0, values=(orig, trans))

                self.status_var.set(
                    f"完成 — {len(texts)} 段文字 | OCR {ocr_ms:.0f}ms | 翻译 {api_ms:.0f}ms")

                # Enable auto toggle now that we have a region
                self.auto_cb.configure(state="normal")

            except Exception as e:
                self.status_var.set(f"错误: {e}")
                messagebox.showerror("错误", str(e))
        finally:
            self.select_btn.configure(state="normal")
            self.root.deiconify()
            self._selecting = False

    def _clear_log(self):
        self._stop_auto()
        self.auto_var.set(False)
        self.auto_cb.configure(state="disabled")
        self._auto_region = None
        self.auto_count_var.set("")

        for item in self.tree.get_children():
            self.tree.delete(item)
        if self.overlay:
            self.overlay.hide()
        self.status_var.set("记录已清空")

    def run(self):
        self.root.mainloop()

    def _on_close(self):
        self._stop_auto()
        if self.overlay:
            self.overlay.destroy()
        self._executor.shutdown(wait=False)
        self.root.destroy()


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
