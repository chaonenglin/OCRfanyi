"""OCRfanyi Control Panel — tkinter UI for region-select translation.

Click "框选翻译区域" → select a screen region → OCR + translate → results
shown in the log panel AND overlaid on screen. Toggle "自动刷新" to keep
monitoring the same region continuously.
"""

import os
import time
import hashlib
import threading
import asyncio
import ctypes
import ctypes.wintypes
import numpy as np
import tkinter as tk
from tkinter import ttk, messagebox
from concurrent.futures import ThreadPoolExecutor

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, FONT_PATH, LANGUAGES, DEFAULT_SOURCE_LANG
from src.capture.screen_capture import ScreenCapture
from src.ocr.paddle_ocr import EasyOCREngine
from src.overlay.selection_window import SelectionWindow
from src.overlay.overlay_window import OverlayWindow
from src.overlay.text_renderer import TextRenderer

def _make_system_prompt(source_lang):
    return f"""You are a {source_lang}-to-Chinese translator. Translate each line to natural, fluent Simplified Chinese.
- Input lines are separated by "|||". Output translations separated by "|||" in the exact same order.
- Lines are in reading order (top-to-bottom, left-to-right). Neighboring lines may form a paragraph — translate them coherently, preserving paragraph flow.
- Return ONLY the translations. No explanations, notes, or extra text.
- Keep non-{source_lang} text unchanged."""

# Win32 constants
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_CLOSE = 0x0010
HK_ID_TRANS = 1
HK_ID_HIDE = 2

# Virtual-key to display name mapping for common keys
_VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x13: "Pause",
    0x14: "CapsLock", 0x1B: "Esc", 0x20: "Space", 0x21: "PageUp",
    0x22: "PageDown", 0x23: "End", 0x24: "Home", 0x25: "Left",
    0x26: "Up", 0x27: "Right", 0x28: "Down", 0x2C: "PrintScreen",
    0x2D: "Insert", 0x2E: "Delete",
    0x30: "0", 0x31: "1", 0x32: "2", 0x33: "3", 0x34: "4",
    0x35: "5", 0x36: "6", 0x37: "7", 0x38: "8", 0x39: "9",
    0x41: "A", 0x42: "B", 0x43: "C", 0x44: "D", 0x45: "E",
    0x46: "F", 0x47: "G", 0x48: "H", 0x49: "I", 0x4A: "J",
    0x4B: "K", 0x4C: "L", 0x4D: "M", 0x4E: "N", 0x4F: "O",
    0x50: "P", 0x51: "Q", 0x52: "R", 0x53: "S", 0x54: "T",
    0x55: "U", 0x56: "V", 0x57: "W", 0x58: "X", 0x59: "Y", 0x5A: "Z",
    0x60: "Num0", 0x61: "Num1", 0x62: "Num2", 0x63: "Num3",
    0x64: "Num4", 0x65: "Num5", 0x66: "Num6", 0x67: "Num7",
    0x68: "Num8", 0x69: "Num9", 0x6A: "Num*", 0x6B: "Num+",
    0x6D: "Num-", 0x6E: "Num.", 0x6F: "Num/",
    0x70: "F1", 0x71: "F2", 0x72: "F3", 0x73: "F4",
    0x74: "F5", 0x75: "F6", 0x76: "F7", 0x77: "F8",
    0x78: "F9", 0x79: "F10", 0x7A: "F11", 0x7B: "F12",
    0xA0: "LShift", 0xA1: "RShift", 0xA2: "LCtrl", 0xA3: "RCtrl",
    0xA4: "LAlt", 0xA5: "RAlt",
}

def _vk_name(vk):
    return _VK_NAMES.get(vk, f"Key({vk})")


def _run_translate(texts, api_key, base_url, system_prompt):
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
                {"role": "system", "content": system_prompt},
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


def _hotkey_thread(trigger_trans, trigger_hide, stop_event, vk_trans, vk_hide, on_error):
    """Dedicated thread: message-only window for TWO global hotkeys."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_void_p,
                                  ctypes.c_uint, ctypes.c_ulonglong,
                                  ctypes.c_ulonglong)

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", ctypes.c_uint),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", ctypes.c_void_p),
            ("hIcon", ctypes.c_void_p),
            ("hCursor", ctypes.c_void_p),
            ("hbrBackground", ctypes.c_void_p),
            ("lpszMenuName", ctypes.c_wchar_p),
            ("lpszClassName", ctypes.c_wchar_p),
        ]

    user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.c_ulonglong, ctypes.c_ulonglong]

    hinst = kernel32.GetModuleHandleW(None)
    done = False

    @WNDPROC
    def wnd_proc(hwnd, msg, wparam, lparam):
        nonlocal done
        if msg == WM_HOTKEY:
            if wparam == HK_ID_TRANS:
                trigger_trans.set()
            elif wparam == HK_ID_HIDE:
                trigger_hide.set()
            return 0
        elif msg == WM_CLOSE:
            done = True
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    wc = WNDCLASSW()
    wc.lpfnWndProc = wnd_proc
    wc.hInstance = hinst
    wc.lpszClassName = "OCRFanyiHotkey"
    user32.RegisterClassW(ctypes.byref(wc))

    hwnd = user32.CreateWindowExW(
        0, "OCRFanyiHotkey", None, 0,
        0, 0, 0, 0, None, None, hinst, None
    )

    ok = user32.RegisterHotKey(hwnd, HK_ID_TRANS, MOD_NOREPEAT, vk_trans)
    ok = user32.RegisterHotKey(hwnd, HK_ID_HIDE, MOD_NOREPEAT, vk_hide) and ok
    if not ok:
        on_error()
        user32.UnregisterHotKey(hwnd, HK_ID_TRANS)
        user32.UnregisterHotKey(hwnd, HK_ID_HIDE)
        user32.DestroyWindow(hwnd)
        return

    msg = ctypes.wintypes.MSG()
    while not done:
        if user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    user32.UnregisterHotKey(hwnd, HK_ID_TRANS)
    user32.UnregisterHotKey(hwnd, HK_ID_HIDE)
    user32.DestroyWindow(hwnd)


class RoundedButton(tk.Canvas):
    """Canvas-based rounded button with hover effect."""
    def __init__(self, parent, text, command, width=200, height=38,
                 bg='#a0785a', hover_bg='#ba8e6e', fg='white',
                 radius=10, font_size=11, **kwargs):
        super().__init__(parent, width=width, height=height,
                        highlightthickness=0, borderwidth=0, **kwargs)
        self._text = text
        self._command = command
        self._bg = bg
        self._hover_bg = hover_bg
        self._fg = fg
        self._radius = radius
        self._font = ('Microsoft YaHei', font_size, 'bold')
        self._enabled = True
        self.configure(bg='#faf5f0')
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", lambda e: self._draw(True))
        self.bind("<Leave>", lambda e: self._draw(False))
        self._after_id = None

    def _draw(self, hover=False):
        self.delete("all")
        w = self.winfo_width() or 200
        h = self.winfo_height() or 38
        r = self._radius
        color = self._hover_bg if hover and self._enabled else self._bg
        if not self._enabled:
            color = '#b0a89e'
        # Rounded rectangle
        self.create_rounded_rect(2, 2, w - 2, h - 2, r, fill=color, outline='')
        self.create_text(w // 2, h // 2, text=self._text, fill=self._fg,
                        font=self._font, anchor='center')

    def create_rounded_rect(self, x1, y1, x2, y2, r, **kwargs):
        pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1,
               x2, y1, x2, y1 + r, x2, y1 + r, x2, y2 - r,
               x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2,
               x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r,
               x1, y2 - r, x1, y1 + r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, **kwargs)

    def _on_click(self, event):
        if self._enabled and self._command:
            self._command()

    def set_enabled(self, enabled):
        self._enabled = enabled
        self._draw()

    def set_text(self, text):
        self._text = text
        self._draw()

    def pack(self, **kw):
        super().pack(**kw)
        self.after(10, lambda: self._draw())


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("by：超能力是超能吃 — 截图翻译")
        self.root.geometry("460x584")
        self.root.resizable(True, True)
        self.root.minsize(360, 400)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.configure(bg='#2a1f1a')

        # Color scheme — minimal, let background image show through
        self._C = {
            'bg': '#2a1f1a',         # root bg (dark brown)
            'wg_bg': '#faf5f0',      # widget background on cards
            'btn_bg': '#efe8e0',     # button background
            'text': '#000000', 'text_dim': '#333333',
            'btn': '#a0785a', 'btn_hover': '#ba8e6e',
        }
        self._WIDGET_BG = self._C['wg_bg']

        self._load_background()

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
        self._translating = False

        self._auto_region = None
        self._auto_active = False
        self._auto_job_id = None
        self._auto_count = 0

        self._source_lang = DEFAULT_SOURCE_LANG
        self._switching_lang = False
        self._paused = False
        self._hk_trans_vk = 0x52
        self._hk_hide_vk = 0x54
        self._hk_trigger_trans = threading.Event()
        self._hk_trigger_hide = threading.Event()
        self._hk_stop = threading.Event()
        self._hk_listen_target = None

        self._build_ui()
        self.root.after(100, self._init_engines)
        self.root.after(200, self._start_hotkey_thread)
        self.root.after(300, self._poll_hotkey)

    def _load_background(self):
        bg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "beijingtu", "fe1f8d99b732b01cb4af12a99f0fe576.jpg")
        try:
            from PIL import Image, ImageTk
            img = Image.open(bg_path)
            w, h = img.size
            target_w, target_h = 460, 584
            ratio = max(target_w / w, target_h / h)
            new_w, new_h = int(w * ratio), int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - target_w) // 2
            top = (new_h - target_h) // 2
            img = img.crop((left, top, left + target_w, top + target_h))
            self._bg_image = ImageTk.PhotoImage(img)
        except Exception:
            self._bg_image = None

    def _build_ui(self):
        W, H = 460, 584

        # Build composite bg image: bg photo + semi-transparent card overlays
        self._composite_bg = None
        if self._bg_image:
            from PIL import Image, ImageDraw, ImageTk
            pil_img = Image.open(os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "beijingtu", "fe1f8d99b732b01cb4af12a99f0fe576.jpg"))
            iw, ih = pil_img.size
            r = max(W / iw, H / ih)
            nw, nh = int(iw * r), int(ih * r)
            pil_img = pil_img.resize((nw, nh), Image.LANCZOS)
            left = (nw - W) // 2
            top_tree = (nh - H) // 2
            pil_img = pil_img.crop((left, top_tree, left + W, top_tree + H)).convert("RGBA")

            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            card_color = (255, 248, 240, 80)  # warm light, ~31% opacity

            cards = [
                (8, 8, W - 8, 42),     # API key
                (8, 46, W - 8, 70),    # language selector
                (8, 76, W - 8, 120),   # select button
                (8, 126, W - 8, 152),  # auto refresh
                (8, 158, W - 8, 184),  # translate hotkey
                (8, 190, W - 8, 216),  # hide hotkey
                (8, 222, W - 8, 524),  # log area
                (8, 534, W - 8, 576),  # bottom bar
            ]
            for cx1, cy1, cx2, cy2 in cards:
                draw.rounded_rectangle([cx1, cy1, cx2, cy2], radius=8,
                                       fill=card_color, outline=None)

            pil_img = Image.alpha_composite(pil_img, overlay)
            self._composite_bg = ImageTk.PhotoImage(pil_img)

        # Canvas fills the window
        self._canvas = tk.Canvas(self.root, width=W, height=H,
                                 highlightthickness=0, borderwidth=0,
                                 bg=self._C['bg'])
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        if self._composite_bg:
            self._canvas.create_image(0, 0, anchor='nw', image=self._composite_bg)

        # Helper: Canvas-native text (transparent — shows background image through)
        def _ct(x, y, text, anchor='w', fill=None, font_spec=None, bold=True):
            if fill is None:
                fill = self._C['text']
            if font_spec is None:
                fw = 'bold' if bold else 'normal'
                font_spec = ('Microsoft YaHei', 9, fw)
            return self._canvas.create_text(x, y, text=text, anchor=anchor,
                                            fill=fill, font=font_spec)

        # Helper: place a widget on the Canvas
        def _cw(widget, x, y, w, h, anchor='nw'):
            self._canvas.create_window(x, y, window=widget, width=w, height=h,
                                       anchor=anchor)

        wbg = self._WIDGET_BG

        # Helper: Canvas clickable text button (transparent, shows bg through)
        def _mk_btn(x, y, text, command, anchor='w', fill=None,
                    hover_fill=None, font_spec=None):
            if fill is None:
                fill = self._C['text']
            if hover_fill is None:
                hover_fill = self._C['btn']
            if font_spec is None:
                font_spec = ('Microsoft YaHei', 9, 'bold')
            item = self._canvas.create_text(x, y, text=text, anchor=anchor,
                                            fill=fill, font=font_spec)
            state = {'id': item, 'command': command, 'enabled': True,
                     'text': text, 'fill': fill, 'hover_fill': hover_fill}
            def _enter(e):
                if state['enabled']:
                    self._canvas.itemconfigure(item, fill=hover_fill)
                    self._canvas.configure(cursor='hand2')
            def _leave(e):
                self._canvas.itemconfigure(item, fill=state['fill'] if state['enabled'] else self._C['text_dim'])
                self._canvas.configure(cursor='')
            def _click(e):
                if state['enabled'] and state['command']:
                    state['command']()
            self._canvas.tag_bind(item, '<Enter>', _enter)
            self._canvas.tag_bind(item, '<Leave>', _leave)
            self._canvas.tag_bind(item, '<Button-1>', _click)
            return state

        # ── row 1: API Key ──
        _ct(16, 26, "API Key:")
        self.api_key_var = tk.StringVar(value=DEEPSEEK_API_KEY)
        self.api_entry = tk.Entry(self.root, textvariable=self.api_key_var,
                                  show="*", width=28, bg='white',
                                  fg=self._C['text'], insertbackground=self._C['text'],
                                  font=('Consolas', 9, 'bold'), relief=tk.FLAT, bd=3)
        _cw(self.api_entry, 72, 16, 200, 28)

        self._btn_show = _mk_btn(300, 30, "显示", self._toggle_key)

        # ── row 2: Language selector ──
        _ct(16, 58, "源语言:")
        lang_info = LANGUAGES[self._source_lang]
        self.lang_var = tk.StringVar(value=lang_info["label"])
        lang_combo = ttk.Combobox(self.root, textvariable=self.lang_var,
                                  values=[v["label"] for v in LANGUAGES.values()],
                                  state="readonly", width=14,
                                  font=('Microsoft YaHei', 9))
        self.lang_combo = lang_combo
        lang_combo.bind("<<ComboboxSelected>>", self._on_lang_change)
        _cw(lang_combo, 72, 48, 140, 24)

        # ── row 3: Select button ──
        self.select_btn = RoundedButton(self.root, "框选翻译区域",
                                        command=self._on_select,
                                        width=W - 24, height=42, radius=10,
                                        bg=self._C['btn'],
                                        hover_bg=self._C['btn_hover'],
                                        font_size=13)
        self.select_btn.place(x=12, y=78)
        self.select_btn.set_enabled(False)

        # ── row 4: Auto refresh ──
        self.auto_var = tk.BooleanVar(value=False)
        cb = tk.Checkbutton(self.root, text="自动刷新",
                           variable=self.auto_var, command=self._on_auto_toggle,
                           bg=wbg, fg=self._C['text'],
                           selectcolor=wbg, activebackground=wbg,
                           font=('Microsoft YaHei', 9, 'bold'))
        _cw(cb, 16, 132, 200, 24)
        self.auto_cb = cb

        self.auto_count_var = tk.StringVar(value="")
        self._ci_auto_count = _ct(340, 137, "", fill=self._C['text_dim'])
        self.auto_count_var.trace_add("write", lambda *_: self._canvas.itemconfigure(
            self._ci_auto_count, text=self.auto_count_var.get()))

        # ── row 5: Translate hotkey ──
        _ct(16, 168, "翻译热键:")

        self._hk_trans_name_var = tk.StringVar(value=_vk_name(self._hk_trans_vk))
        self._ci_hk_trans_name = _ct(86, 168, self._hk_trans_name_var.get(), bold=True)
        self._hk_trans_name_var.trace_add("write", lambda *_: self._canvas.itemconfigure(
            self._ci_hk_trans_name, text=self._hk_trans_name_var.get()))

        self._btn_hk_trans = _mk_btn(145, 172, "自定义",
                                     lambda: self._on_customize_hotkey('trans'))
        _ct(210, 168, "翻译 (会禁用按键功能)", fill=self._C['text_dim'])

        # ── row 6: Hide hotkey ──
        _ct(16, 200, "隐藏热键:")

        self._hk_hide_name_var = tk.StringVar(value=_vk_name(self._hk_hide_vk))
        self._ci_hk_hide_name = _ct(86, 200, self._hk_hide_name_var.get(), bold=True)
        self._hk_hide_name_var.trace_add("write", lambda *_: self._canvas.itemconfigure(
            self._ci_hk_hide_name, text=self._hk_hide_name_var.get()))

        self._btn_hk_hide = _mk_btn(145, 204, "自定义",
                                    lambda: self._on_customize_hotkey('hide'))
        _ct(210, 200, "隐藏叠加层 (会禁用按键功能)", fill=self._C['text_dim'])

        # ── row 7: Log area ──
        _ct(18, 236, "翻译记录", fill=self._C['text_dim'], bold=True)

        # ttk style for Treeview — match card background
        style = ttk.Style()
        style.configure('Card.Treeview',
                        background='#faf5f0',
                        fieldbackground='#faf5f0',
                        foreground=self._C['text'])
        style.map('Card.Treeview', background=[('selected', self._C['btn'])])

        columns = ("source", "target")
        self.tree = ttk.Treeview(self.root, columns=columns, show="headings",
                                  height=14, selectmode="extended",
                                  style='Card.Treeview')
        self.tree.heading("source", text="原文")
        self.tree.heading("target", text="翻译")
        self.tree.column("source", width=190, minwidth=70)
        self.tree.column("target", width=190, minwidth=70)
        scrollbar = ttk.Scrollbar(self.root, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        _cw(self.tree, 18, 248, 394, 274)
        _cw(scrollbar, 414, 248, 12, 274)

        # ── row 8: Bottom bar ──
        self.status_var = tk.StringVar(value="正在加载 OCR 模型，请稍候...")
        self._ci_status = _ct(18, 550, self.status_var.get(),
                              fill=self._C['text_dim'],
                              font_spec=('Microsoft YaHei', 8, 'bold'))
        self.status_var.trace_add("write", lambda *_: self._canvas.itemconfigure(
            self._ci_status, text=self.status_var.get()))

        self._btn_pause = _mk_btn(200, 554, "暂停 (释放按键原功能)",
                                  self._on_pause_toggle)
        self._btn_clear = _mk_btn(356, 554, "清空记录", self._clear_log)

    def _toggle_key(self):
        cur = self.api_entry.cget("show")
        if cur == "*":
            self.api_entry.configure(show="")
            self._canvas.itemconfigure(self._btn_show['id'], text="隐藏")
            self._btn_show['text'] = "隐藏"
        else:
            self.api_entry.configure(show="*")
            self._canvas.itemconfigure(self._btn_show['id'], text="显示")
            self._btn_show['text'] = "显示"

    def _on_lang_change(self, event=None):
        selected_label = self.lang_var.get()
        new_lang = self._source_lang
        for key, info in LANGUAGES.items():
            if info["label"] == selected_label:
                new_lang = key
                break
        if new_lang == self._source_lang and self.ocr_engine is not None:
            return
        self._source_lang = new_lang
        self._switching_lang = True
        self.lang_combo.configure(state="disabled")
        self.status_var.set(f"正在加载 {LANGUAGES[new_lang]['label']} 模型...")
        self._executor.submit(self._init_ocr).add_done_callback(
            lambda fut: self.root.after(0, self._on_lang_switch_done, fut))

    def _init_ocr(self):
        new_engine = EasyOCREngine(lang_list=LANGUAGES[self._source_lang]["ocr_list"])
        old = self.ocr_engine
        self.ocr_engine = new_engine
        if old is not None:
            del old
            import torch
            torch.cuda.empty_cache()

    def _on_lang_switch_done(self, future):
        self._switching_lang = False
        try:
            future.result()
            self.status_var.set(f"已切换到 {self.lang_var.get()}")
        except Exception as e:
            self.status_var.set(f"语言切换失败: {e}")
        finally:
            self.lang_combo.configure(state="readonly")

    def _on_pause_toggle(self):
        self._paused = not self._paused
        if self._paused:
            self._canvas.itemconfigure(self._btn_pause['id'], text="继续")
            self._btn_pause['text'] = "继续"
            if self._auto_active:
                self._stop_auto()
                self._was_auto = True
            else:
                self._was_auto = False
            if self.overlay:
                self.overlay.hide()
            self._stop_hotkey_thread()
            self.status_var.set("已暂停 — 按键功能已释放")
        else:
            self._canvas.itemconfigure(self._btn_pause['id'], text="暂停 (释放按键原功能)")
            self._btn_pause['text'] = "暂停 (释放按键原功能)"
            self._start_hotkey_thread()
            if self._was_auto:
                self._start_auto()
                self.auto_var.set(True)
            self.status_var.set("已恢复")

    def _init_engines(self):
        try:
            self.capture = ScreenCapture()
            self.renderer = TextRenderer(self.capture.width, self.capture.height)
            self.overlay = OverlayWindow(self.capture.width, self.capture.height)
            self.root.update()

            self.ocr_engine = EasyOCREngine(lang_list=LANGUAGES[self._source_lang]["ocr_list"])
            self.status_var.set("就绪 — 点击上方按钮框选翻译区域")
            self.select_btn.set_enabled(True)
        except Exception as e:
            self.status_var.set(f"初始化失败: {e}")
            messagebox.showerror("初始化错误", str(e))

    # ── Hotkey management ──────────────────────────────────────────

    def _show_translating_overlay(self):
        """Render and display a large '翻译中...' text in the selected region."""
        if self._auto_region is None:
            return
        from PIL import Image, ImageDraw, ImageFont
        x, y, w, h = self._auto_region
        img = Image.new("RGBA", (self.capture.width, self.capture.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype(FONT_PATH, 48)
        except OSError:
            font = ImageFont.load_default()
        text = "翻译中..."
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        cx = x + w // 2 - tw // 2
        cy = y + h // 2 - th // 2
        draw.rectangle([cx - 14, cy - 10, cx + tw + 14, cy + th + 10],
                       fill=(30, 30, 30, 200))
        draw.text((cx, cy), text, font=font, fill=(255, 255, 255, 255))
        self.overlay.update(np.array(img))
        self.overlay.show()

    def _on_customize_hotkey(self, which):
        if self._hk_listen_target is not None:
            return
        self._hk_listen_target = which
        if which == 'trans':
            btn = self._btn_hk_trans
            self._hk_trans_name_var.set("...")
        else:
            btn = self._btn_hk_hide
            self._hk_hide_name_var.set("...")
        self._canvas.itemconfigure(btn['id'], text="请按键...",
                                   fill=self._C['text_dim'])
        btn['enabled'] = False
        btn['text'] = "请按键..."
        self.root.bind("<KeyPress>", self._on_key_capture)
        self.root.focus_set()

    def _on_key_capture(self, event):
        which = self._hk_listen_target
        if which is None:
            return "break"
        self._hk_listen_target = None
        # Re-enable both buttons
        for btn in (self._btn_hk_trans, self._btn_hk_hide):
            self._canvas.itemconfigure(btn['id'], text="自定义", fill=btn['fill'])
            btn['enabled'] = True
            btn['text'] = "自定义"
        self.root.unbind("<KeyPress>")

        if event.keycode == 0x1B:  # Esc cancels
            if which == 'trans':
                self._hk_trans_name_var.set(_vk_name(self._hk_trans_vk))
            else:
                self._hk_hide_name_var.set(_vk_name(self._hk_hide_vk))
            return "break"

        if which == 'trans':
            self._hk_trans_vk = event.keycode
            self._hk_trans_name_var.set(_vk_name(self._hk_trans_vk))
        else:
            self._hk_hide_vk = event.keycode
            self._hk_hide_name_var.set(_vk_name(self._hk_hide_vk))
        self._restart_hotkey_thread()
        return "break"

    def _start_hotkey_thread(self):
        if self._hk_stop.is_set():
            return
        self._stop_hotkey_thread()

        self._hk_stop.clear()
        self._hk_trigger_trans.clear()
        self._hk_trigger_hide.clear()

        def _on_err():
            self.root.after(0, lambda: messagebox.showwarning(
                "快捷键注册失败",
                "一个或多个快捷键已被占用，请更换"))

        t = threading.Thread(
            target=_hotkey_thread,
            args=(self._hk_trigger_trans, self._hk_trigger_hide, self._hk_stop,
                  self._hk_trans_vk, self._hk_hide_vk, _on_err),
            daemon=True,
        )
        t.start()
        self._hk_thread = t

    def _stop_hotkey_thread(self):
        if hasattr(self, '_hk_thread') and self._hk_thread is not None:
            if self._hk_thread.is_alive():
                hwnd = ctypes.windll.user32.FindWindowW("OCRFanyiHotkey", None)
                if hwnd:
                    ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                self._hk_thread.join(timeout=1.0)
            self._hk_thread = None

    def _restart_hotkey_thread(self):
        self._stop_hotkey_thread()
        self._start_hotkey_thread()

    def _poll_hotkey(self):
        """Periodically check both hotkey events (thread-safe polling)."""
        if self._hk_trigger_trans.is_set():
            self._hk_trigger_trans.clear()
            self._on_hotkey_translate()
        if self._hk_trigger_hide.is_set():
            self._hk_trigger_hide.clear()
            self._on_hotkey_hide()
        self.root.after(50, self._poll_hotkey)

    def _on_hotkey_hide(self):
        """Hide the translation overlay."""
        if self.overlay:
            self.overlay.hide()
        self.status_var.set("叠加层已隐藏")

    def _on_hotkey_translate(self):
        """One-shot translate of the selected region via hotkey."""
        if self._paused or self._selecting or self._translating or self._switching_lang:
            return
        if self._auto_region is None:
            self.status_var.set("请先框选翻译区域")
            return

        api_key = self.api_key_var.get().strip()
        if not api_key:
            self.status_var.set("请先设置 API Key")
            return

        self._translating = True
        x, y, w, h = self._auto_region

        try:
            # Capture
            img = self.capture.capture_region(x, y, w, h)

            # OCR
            t0 = time.time()
            results = self.ocr_engine.detect_and_recognize(img)
            ocr_ms = (time.time() - t0) * 1000

            if not results:
                self.status_var.set(f"热键翻译 — 未检测到文字 ({ocr_ms:.0f}ms)")
                return

            texts = []
            entries = []
            for (bx, by, bw, bh), text, conf in results:
                abs_bbox = (x + bx, y + by, bw, bh)
                entries.append((abs_bbox, text))
                texts.append(text)

            self.status_var.set(f"检测到 {len(texts)} 段文字，翻译中...")
            self._show_translating_overlay()

            # Translate
            t1 = time.time()
            future = self._executor.submit(
                _run_translate, texts, api_key, DEEPSEEK_BASE_URL,
                _make_system_prompt(LANGUAGES[self._source_lang]["prompt_lang"]))
            translations = future.result(timeout=30)
            api_ms = (time.time() - t1) * 1000

            # Display
            entry_list = [(b, trans) for (b, _), trans in zip(entries, translations)]
            bitmap = self.renderer.render(entry_list)
            self.overlay.update(bitmap)
            self.overlay.show()

            for item in self.tree.get_children():
                self.tree.delete(item)
            for (_, orig), trans in zip(entries, translations):
                self.tree.insert("", 0, values=(orig, trans))

            self.status_var.set(
                f"热键翻译完成 — {len(texts)} 段 | OCR {ocr_ms:.0f}ms | API {api_ms:.0f}ms")

        except Exception as e:
            self.status_var.set(f"热键翻译失败: {e}")
        finally:
            self._translating = False
            self._hk_trigger_trans.clear()  # discard queued spam presses

    # ── Auto-refresh ────────────────────────────────────────────────

    def _on_auto_toggle(self):
        if self.auto_var.get():
            if self._auto_region is None:
                self.status_var.set("请先框选翻译区域")
                self.auto_var.set(False)
                return
            self._start_auto()
        else:
            self._stop_auto()

    def _start_auto(self):
        if self._auto_region is None or self._auto_active:
            return
        self._auto_active = True
        self._auto_count = 0
        self.select_btn.set_enabled(False)
        self.status_var.set("自动刷新中...")
        self._auto_tick()

    def _stop_auto(self):
        self._auto_active = False
        if self._auto_job_id:
            self.root.after_cancel(self._auto_job_id)
            self._auto_job_id = None
        self.select_btn.set_enabled(True)
        if self.overlay:
            self.overlay.hide()
        self.auto_count_var.set("")
        self.status_var.set("自动刷新已停止")

    def _auto_tick(self):
        """One tick: capture → hash check → OCR → (async translate)."""
        if not self._auto_active or self._auto_region is None:
            return
        if self._switching_lang:
            self._auto_job_id = self.root.after(300, self._auto_tick)
            return

        try:
            x, y, w, h = self._auto_region
            api_key = self.api_key_var.get().strip()

            img = self.capture.capture_region(x, y, w, h)

            hh = hashlib.md5(img.tobytes()).digest()
            if self._last_auto_hash is not None and hh == self._last_auto_hash:
                # No change — wait 300ms then retry
                self._auto_job_id = self.root.after(300, self._auto_tick)
                return
            self._last_auto_hash = hh

            t0 = time.time()
            results = self.ocr_engine.detect_and_recognize(img)
            ocr_ms = (time.time() - t0) * 1000

            if not results:
                self.status_var.set(f"#{self._auto_count} OCR ({ocr_ms:.0f}ms) — 无变化")
                self._auto_job_id = self.root.after(300, self._auto_tick)
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
            self._show_translating_overlay()

            self._executor.submit(_run_translate, texts, api_key, DEEPSEEK_BASE_URL,
                _make_system_prompt(LANGUAGES[self._source_lang]["prompt_lang"])
                ).add_done_callback(
                    lambda fut, e=entries, st=time.time():
                        self.root.after(0, self._on_auto_translate_done, fut, e, st))

        except Exception as e:
            self.status_var.set(f"自动刷新错误: {e}")
            self._auto_job_id = self.root.after(500, self._auto_tick)

    def _on_auto_translate_done(self, future, entries, t_start):
        if not self._auto_active:
            return

        try:
            translations = future.result(timeout=5)
        except Exception as e:
            self.status_var.set(f"翻译失败: {e}")
            self._auto_job_id = self.root.after(500, self._auto_tick)
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

        # Chain: immediately start next cycle
        if self._auto_active:
            self._auto_job_id = self.root.after(200, self._auto_tick)

    # ── Manual select ───────────────────────────────────────────────

    def _on_select(self):
        if self._selecting:
            return
        self._selecting = True

        was_auto = self._auto_active
        if was_auto:
            self._stop_auto()
            self.auto_var.set(False)

        self.select_btn.set_enabled(False)

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

            # 在显示框选层之前 capture_full() 冻结全屏快照。
            # SelectionWindow 用 snapshot 铺暗化底图，仅在拖拽矩形内露出原色。
            # OCR 使用 snapshot 的切片，保证「所见即所识别」。
            snapshot = self.capture.capture_full()

            try:
                win = SelectionWindow(self.capture.width, self.capture.height,
                                      snapshot=snapshot)
                rect = win.run()
                win.destroy()

                if rect is None or rect[2] < 10 or rect[3] < 10:
                    self.status_var.set("已取消")
                    return

                x, y, w, h = rect
                self._auto_region = (x, y, w, h)
                self._last_auto_hash = None

                self.status_var.set(f"选中 ({x},{y}) {w}x{h}，正在 OCR...")
                self.root.update()

                t0 = time.time()
                img = snapshot[y:y + h, x:x + w].copy()
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
                self._show_translating_overlay()

                t1 = time.time()
                future = self._executor.submit(
                    _run_translate, texts, api_key, DEEPSEEK_BASE_URL,
                    _make_system_prompt(LANGUAGES[self._source_lang]["prompt_lang"]))
                translations = future.result(timeout=30)
                api_ms = (time.time() - t1) * 1000

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


            except Exception as e:
                self.status_var.set(f"错误: {e}")
                messagebox.showerror("错误", str(e))
        finally:
            self.select_btn.set_enabled(True)
            self.root.deiconify()
            self._selecting = False

    def _clear_log(self):
        self._stop_auto()
        self.auto_var.set(False)
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
        self._stop_hotkey_thread()
        if self.overlay:
            self.overlay.destroy()
        self._executor.shutdown(wait=False)
        self.root.destroy()


def main():
    app = App()
    app.run()


if __name__ == "__main__":
    main()
