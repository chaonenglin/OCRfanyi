"""Full-screen selection overlay for choosing a region to translate.

User drags a rectangle; the selected area appears as a transparent cutout
through a dark overlay. Returns the pixel rect on mouse release.
Esc or right-click cancels.
"""

import ctypes
import ctypes.wintypes
import time
import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
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
    def __init__(self, width, height, snapshot=None):
        global _instance
        _instance = self

        self.width = width
        self.height = height
        self.hwnd = None
        self._start = None
        self._end = None
        self.done = False
        self.rect = None

        # 可选冻结快照模式：传入 snapshot 时，全屏显示其暗化副本，
        # 用户框选的矩形区域内恢复为 snapshot 的原始颜色。
        self._dark = None
        self._orig = None
        if snapshot is not None and snapshot.shape[:2] == (height, width):
            orig = np.ascontiguousarray(snapshot, dtype=np.uint8).copy()
            orig[..., 3] = 255
            self._orig = orig
            dark = orig.copy()
            dark[..., :3] = (dark[..., :3].astype(np.uint16) * 4 // 10).astype(np.uint8)
            self._dark = dark

        self._create_window()

        if self._dark is not None:
            self._update_window(self._dark)

        # 构造完成后立刻抢占前台，确保 FocusHost 交接后框选层能接手焦点，
        # run() 入口再调一次兜底（防止消息循环启动前被其他窗口抢走）。
        self._grab_foreground()

    def _grab_foreground(self):
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 5)
            user32.SetForegroundWindow(self.hwnd)

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

        # 去掉 WS_EX_NOACTIVATE：框选层需要能被真正激活，
        # 这样 SetForegroundWindow 才有效，否则焦点仍会落回游戏。
        ex_style = WS_EX_LAYERED | WS_EX_TOPMOST
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
            return 0
        elif msg == WM_KEYDOWN and wparam == VK_ESCAPE:
            self.done = True
            return 0
        elif msg == WM_RBUTTONDOWN:
            self.done = True
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _render(self):
        if self._dark is None:
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
                # 白色边框（1px）
                border_color = (255, 255, 255, 255)
                bitmap[y:y + h, max(0, x - 1):x] = border_color
                bitmap[y:y + h, x + w:x + w + 1] = border_color
                bitmap[max(0, y - 1):y, x:x + w] = border_color
                bitmap[y + h:y + h + 1, x:x + w] = border_color

            self._update_window(bitmap)
            return

        # 冻结快照模式：全屏暗化底图，框选区域内为原色。
        if self._start is None or self._end is None:
            self._update_window(self._dark)
            return

        bitmap = self._dark.copy()
        x = min(self._start[0], self._end[0])
        y = min(self._start[1], self._end[1])
        w = abs(self._end[0] - self._start[0])
        h = abs(self._end[1] - self._start[1])

        if w > 0 and h > 0:
            bitmap[y:y + h, x:x + w] = self._orig[y:y + h, x:x + w]
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
        self._grab_foreground()

        msg = ctypes.wintypes.MSG()
        while not self.done:
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.01)

        user32.ShowWindow(self.hwnd, 0)
        return self.rect

    def destroy(self):
        global _instance
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
        _instance = None
