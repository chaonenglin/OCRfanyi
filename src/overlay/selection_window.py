"""Full-screen selection overlay for choosing a region to translate.

User drags a rectangle; the selected area appears as a transparent cutout
through a dark overlay. Returns the pixel rect on mouse release.
Esc or right-click cancels.
"""

import ctypes
import ctypes.wintypes
import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
WS_POPUP = 0x80000000
ULW_ALPHA = 0x00000002

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEMOVE = 0x0200
WM_RBUTTONDOWN = 0x0204
WM_KEYDOWN = 0x0100
VK_ESCAPE = 0x1B

_instance = None


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_ulonglong, ctypes.c_ulonglong)

user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_ulonglong, ctypes.c_ulonglong]


@WNDPROC
def _wnd_proc(hwnd, msg, wparam, lparam):
    if _instance is not None:
        return _instance._handle_msg(hwnd, msg, wparam, lparam)
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


class SelectionWindow:
    def __init__(self, width, height):
        global _instance
        _instance = self

        self.width = width
        self.height = height
        self.hwnd = None
        self._start = None
        self._end = None
        self.done = False
        self.rect = None
        self._create_window()

    def _create_window(self):
        hinst = kernel32.GetModuleHandleW(None)

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

        wc = WNDCLASSW()
        wc.lpfnWndProc = _wnd_proc
        wc.hInstance = hinst
        wc.lpszClassName = "OCRSelectionOverlay"
        wc.hCursor = user32.LoadCursorW(None, 32512)  # IDC_CROSS
        user32.RegisterClassW(ctypes.byref(wc))

        ex_style = WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE
        self.hwnd = user32.CreateWindowExW(
            ex_style, "OCRSelectionOverlay", None, WS_POPUP,
            0, 0, self.width, self.height,
            None, None, hinst, None
        )

    def _handle_msg(self, hwnd, msg, wparam, lparam):
        if msg == WM_LBUTTONDOWN:
            self._start = (lparam & 0xFFFF, (lparam >> 16) & 0xFFFF)
            self._end = self._start
            self._render()
            return 0
        elif msg == WM_MOUSEMOVE and self._start is not None:
            self._end = (lparam & 0xFFFF, (lparam >> 16) & 0xFFFF)
            self._render()
            return 0
        elif msg == WM_LBUTTONUP and self._start is not None:
            self._end = (lparam & 0xFFFF, (lparam >> 16) & 0xFFFF)
            x = min(self._start[0], self._end[0])
            y = min(self._start[1], self._end[1])
            w = abs(self._end[0] - self._start[0])
            h = abs(self._end[1] - self._start[1])
            if w > 10 and h > 10:
                self.rect = (x, y, w, h)
            self.done = True
            user32.PostQuitMessage(0)
            return 0
        elif msg == WM_KEYDOWN and wparam == VK_ESCAPE:
            self.done = True
            user32.PostQuitMessage(0)
            return 0
        elif msg == WM_RBUTTONDOWN:
            self.done = True
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _render(self):
        if self._start is None or self._end is None:
            return

        bitmap = np.zeros((self.height, self.width, 4), dtype=np.uint8)
        bitmap[:, :, 3] = 100

        x = min(self._start[0], self._end[0])
        y = min(self._start[1], self._end[1])
        w = abs(self._end[0] - self._start[0])
        h = abs(self._end[1] - self._start[1])

        if w > 0 and h > 0:
            bitmap[y:y + h, x:x + w, 3] = 0
            # white border (1px)
            border_color = (255, 255, 255, 255)
            bitmap[y:y + h, max(0, x - 1):x] = border_color
            bitmap[y:y + h, x + w:x + w + 1] = border_color
            bitmap[max(0, y - 1):y, x:x + w] = border_color
            bitmap[y + h:y + h + 1, x:x + w] = border_color

        self._update_window(bitmap)

    def _update_window(self, bitmap):
        h, w, _ = bitmap.shape
        data = np.ascontiguousarray(bitmap, dtype=np.uint8)

        hdc_screen = user32.GetDC(None)

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        hdc_temp = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        gdi32.SelectObject(hdc_temp, hbmp)
        gdi32.SetDIBits(hdc_temp, hbmp, 0, h,
                        data.ctypes.data_as(ctypes.c_void_p),
                        ctypes.byref(bmi), 0)

        blend = (ctypes.c_ubyte * 4)(0, 0, 255, 1)
        sz = ctypes.wintypes.SIZE(w, h)
        pt_src = ctypes.wintypes.POINT(0, 0)
        pt_dst = ctypes.wintypes.POINT(0, 0)

        user32.UpdateLayeredWindow(
            self.hwnd, hdc_screen,
            ctypes.byref(pt_dst), ctypes.byref(sz),
            hdc_temp, ctypes.byref(pt_src), 0,
            ctypes.byref(blend), ULW_ALPHA
        )

        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_temp)
        user32.ReleaseDC(None, hdc_screen)

    def run(self):
        """Block until user selects a region or cancels. Returns (x,y,w,h) or None."""
        user32.ShowWindow(self.hwnd, 5)
        user32.SetForegroundWindow(self.hwnd)

        msg = ctypes.wintypes.MSG()
        while not self.done:
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        user32.ShowWindow(self.hwnd, 0)
        return self.rect

    def destroy(self):
        global _instance
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        _instance = None
