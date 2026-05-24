"""Demo: One-shot region select → OCR → translate → display all at once.

Ctrl+Shift+R : Select a region. OCRs the whole thing, translates all
               detected texts in one API call, displays all results together.
Ctrl+Shift+T : Hide overlay.
Ctrl+C        : Quit.
"""

import sys
import time
import asyncio
import ctypes
import ctypes.wintypes
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, OCR_CONFIDENCE_THRESHOLD, BG_ALPHA
from src.capture.screen_capture import ScreenCapture
from src.ocr.paddle_ocr import EasyOCREngine
from src.overlay.selection_window import SelectionWindow
from src.overlay.overlay_window import OverlayWindow
from src.overlay.text_renderer import TextRenderer

MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000
VK_T = 0x54
VK_R = 0x52
WM_HOTKEY = 0x0312
HOTKEY_ID = 1
HOTKEY_ID_REGION = 2

user32 = ctypes.windll.user32


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.wintypes.WPARAM),
        ("lParam", ctypes.wintypes.LPARAM),
        ("time", ctypes.c_uint32),
        ("pt_x", ctypes.c_int32),
        ("pt_y", ctypes.c_int32),
    ]


SYSTEM_PROMPT = """You are a precise English-to-Chinese translator.
- Translate each line of English text to Simplified Chinese.
- Return ONLY the Chinese translation, one line per input line.
- Input lines are separated by "|||". Output translations separated by "|||" in the exact same order.
- Do NOT add explanations, notes, or any extra text.
- If a line is not English or cannot be translated, keep it unchanged."""


async def translate_batch(session, texts: list[str]) -> list[str]:
    if not texts:
        return []
    joined = "|||".join(texts)
    url = f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
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
    async with session.post(url, json=payload, headers=headers) as resp:
        data = await resp.json()
    if "choices" not in data:
        raise RuntimeError(f"DeepSeek API error: {data}")
    content = data["choices"][0]["message"]["content"].strip()
    translations = [t.strip() for t in content.split("|||")]
    while len(translations) < len(texts):
        translations.append(texts[len(translations)])
    return translations[:len(texts)]


async def do_selection_and_translate(capture, ocr_engine, session, renderer, overlay):
    """One selection cycle: select → OCR → translate → display."""
    print("\n[Demo] Select a region with your mouse...")

    win = SelectionWindow(capture.width, capture.height)
    rect = win.run()
    win.destroy()

    if rect is None or rect[2] < 10 or rect[3] < 10:
        print("[Demo] Cancelled or too small")
        return

    x, y, w, h = rect
    print(f"[Demo] Region: ({x},{y}) {w}x{h}")

    # Step 1: Capture region
    img = capture.capture_region(x, y, w, h)
    print(f"[Demo] Captured {img.shape[1]}x{img.shape[0]}")

    # Step 2: OCR the whole region at once
    t0 = time.time()
    results = ocr_engine.detect_and_recognize(img)
    ocr_ms = (time.time() - t0) * 1000
    print(f"[Demo] OCR found {len(results)} texts in {ocr_ms:.0f}ms")

    if not results:
        print("[Demo] No text found")
        return

    # Step 3: Collect all texts
    entries = []
    texts_to_translate = []
    for (bx, by, bw, bh), text, conf in results:
        abs_bbox = (x + bx, y + by, bw, bh)
        entries.append((abs_bbox, text))
        texts_to_translate.append(text)
        print(f"  [{conf:.2f}] \"{text}\"")

    # Step 4: One API call for ALL texts
    t0 = time.time()
    translations = await translate_batch(session, texts_to_translate)
    api_ms = (time.time() - t0) * 1000
    print(f"[Demo] API returned {len(translations)} translations in {api_ms:.0f}ms")

    # Step 5: Display all at once
    entry_list = []
    for (bbox, _), trans in zip(entries, translations):
        entry_list.append((bbox, trans))
        print(f"  → {trans}")

    bitmap = renderer.render(entry_list)
    overlay.update(bitmap)
    overlay.show()
    print(f"[Demo] Done — all {len(entry_list)} texts displayed together ({ocr_ms:.0f}ms OCR + {api_ms:.0f}ms API)")


async def main_async():
    if not DEEPSEEK_API_KEY:
        print("ERROR: DEEPSEEK_API_KEY environment variable not set.")
        sys.exit(1)

    print("=" * 50)
    print("  OCRfanyi Demo — One-shot region translate")
    print("  Ctrl+Shift+R : Select region & translate")
    print("  Ctrl+Shift+T : Hide overlay")
    print("  Ctrl+C       : Quit")
    print("=" * 50)

    capture = ScreenCapture()
    ocr_engine = EasyOCREngine()
    renderer = TextRenderer(capture.width, capture.height)
    overlay = OverlayWindow(capture.width, capture.height)

    import aiohttp
    async with aiohttp.ClientSession() as session:
        # Register hotkeys
        if user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_T):
            print("Hotkey registered: Ctrl+Shift+T (hide)")
        if user32.RegisterHotKey(None, HOTKEY_ID_REGION, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_R):
            print("Hotkey registered: Ctrl+Shift+R (select & translate)")

        print("[Demo] Ready.\n")

        running = True
        msg = MSG()

        while running:
            result = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
            if result:
                if msg.message == WM_HOTKEY:
                    if msg.wParam == HOTKEY_ID_REGION:
                        await do_selection_and_translate(capture, ocr_engine, session, renderer, overlay)
                    elif msg.wParam == HOTKEY_ID:
                        overlay.hide()
                        print("[Demo] Overlay hidden")
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                await asyncio.sleep(0.03)

        user32.UnregisterHotKey(None, HOTKEY_ID)
        user32.UnregisterHotKey(None, HOTKEY_ID_REGION)
        overlay.destroy()
        print("Goodbye.")


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    main()
