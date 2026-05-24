"""Real-time screen OCR translation overlay.

Captures screen -> OCR (English) -> DeepSeek translation -> overlay.

Hotkey: Ctrl+Shift+T to toggle on/off.
Press Ctrl+C in terminal to quit.
"""

import sys
import time
import ctypes
import ctypes.wintypes

from config import DEEPSEEK_API_KEY
from src.pipeline.coordinator import Coordinator

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


def main():
    if not DEEPSEEK_API_KEY:
        print("ERROR: DEEPSEEK_API_KEY environment variable not set.")
        print("Set it via: export DEEPSEEK_API_KEY=your-key-here")
        sys.exit(1)

    print("=" * 50)
    print("  OCR Translation Overlay")
    print("  Source: English  ->  Target: Chinese")
    print("  Ctrl+Shift+R : Select a region to monitor & translate")
    print("  Ctrl+Shift+T : Pause / Resume monitoring")
    print("  Press Ctrl+C in this terminal to quit")
    print("=" * 50)

    coordinator = Coordinator()

    # Register global hotkeys via ctypes
    if user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_T):
        print("Hotkey registered: Ctrl+Shift+T")
    else:
        print("Warning: Could not register hotkey Ctrl+Shift+T (error: {})".format(
            ctypes.windll.kernel32.GetLastError()))

    if user32.RegisterHotKey(None, HOTKEY_ID_REGION, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_R):
        print("Hotkey registered: Ctrl+Shift+R")
    else:
        print("Warning: Could not register hotkey Ctrl+Shift+R (error: {})".format(
            ctypes.windll.kernel32.GetLastError()))

    coordinator.start()

    # Windows message pump
    try:
        msg = MSG()
        while coordinator.running:
            result = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)  # PM_REMOVE
            if result:
                if msg.message == WM_HOTKEY:
                    if msg.wParam == HOTKEY_ID:
                        coordinator.toggle()
                    elif msg.wParam == HOTKEY_ID_REGION:
                        coordinator.region_translate()
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.03)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)
        user32.UnregisterHotKey(None, HOTKEY_ID_REGION)
        coordinator.stop()
        print("Goodbye.")


if __name__ == "__main__":
    main()
