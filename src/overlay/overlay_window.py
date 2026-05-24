import ctypes
import ctypes.wintypes
import numpy as np

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32


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


def _wnd_proc(hwnd, msg, wparam, lparam):
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_ulonglong, ctypes.c_ulonglong]

_WND_PROC = WNDPROC(_wnd_proc)


class OverlayWindow:
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOPMOST = 0x00000008
    WS_EX_NOACTIVATE = 0x08000000
    WS_POPUP = 0x80000000
    ULW_ALPHA = 0x00000002

    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.hwnd = None
        self._visible = False
        self._create_window()

    def _create_window(self):
        hinst = kernel32.GetModuleHandleW(None)

        wnd_class = WNDCLASSW()
        wnd_class.lpfnWndProc = _WND_PROC
        wnd_class.hInstance = hinst
        wnd_class.lpszClassName = "OCRTranslatorOverlay"

        user32.RegisterClassW(ctypes.byref(wnd_class))

        ex_style = self.WS_EX_LAYERED | self.WS_EX_TRANSPARENT | \
                   self.WS_EX_TOPMOST | self.WS_EX_NOACTIVATE

        self.hwnd = user32.CreateWindowExW(
            ex_style, "OCRTranslatorOverlay", None, self.WS_POPUP,
            0, 0, self.width, self.height,
            None, None, hinst, None
        )

    def update(self, bitmap: np.ndarray):
        h, w, _ = bitmap.shape
        bitmap_flat = np.ascontiguousarray(bitmap, dtype=np.uint8)

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
                        bitmap_flat.ctypes.data_as(ctypes.c_void_p),
                        ctypes.byref(bmi), 0)

        blend = (ctypes.c_ubyte * 4)(0, 0, 255, 1)  # AC_SRC_OVER, 0, 255, AC_SRC_ALPHA
        sz = ctypes.wintypes.SIZE(w, h)
        pt_src = ctypes.wintypes.POINT(0, 0)
        pt_dst = ctypes.wintypes.POINT(0, 0)

        user32.UpdateLayeredWindow(
            self.hwnd, hdc_screen,
            ctypes.byref(pt_dst), ctypes.byref(sz),
            hdc_temp, ctypes.byref(pt_src), 0,
            ctypes.byref(blend), self.ULW_ALPHA
        )

        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_temp)
        user32.ReleaseDC(None, hdc_screen)

        if not self._visible:
            user32.ShowWindow(self.hwnd, 5)
            self._visible = True

    def hide(self):
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 0)
        self._visible = False

    def show(self):
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 5)
        self._visible = True

    def destroy(self):
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
