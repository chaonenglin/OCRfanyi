"""屏外可激活占位窗口，在翻译会话期间持有系统前台。

主要针对视觉小说等类型的游戏：
- 此类游戏通常在失去前台时暂停音频，让本进程持有前台可避免
  翻译流程中焦点短暂回到游戏导致 BGM 闪烁的问题。
- 此类游戏会在自身拥有前台时轮询鼠标状态来推进对话；
  让本进程持有前台，可避免框选区域时的鼠标点击被游戏误处理。

译文浮层仍保持 NOACTIVATE / 鼠标穿透，前台由本窗口单独负责。
"""

import ctypes
import ctypes.wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_POPUP = 0x80000000
SW_SHOW = 5
SW_HIDE = 0


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_ulonglong, ctypes.c_ulonglong)

user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_ulonglong, ctypes.c_ulonglong]


@WNDPROC
def _wnd_proc(hwnd, msg, wparam, lparam):
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


class FocusHost:
    def __init__(self):
        self.hwnd = None
        self._create()

    def _create(self):
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
        wc.lpszClassName = "OCRFocusHost"
        user32.RegisterClassW(ctypes.byref(wc))

        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW, "OCRFocusHost", None, WS_POPUP,
            -32000, -32000, 1, 1, None, None, hinst, None,
        )

    def begin(self):
        """开启焦点会话：让替身窗口出现并抢占前台。
        游戏检测到前台不是自己时会暂停音频，整个翻译流程都由替身持有前台，
        从而避免中途短暂把焦点还给游戏导致 BGM 闪一下的问题。
        """
        if self.hwnd:
            user32.ShowWindow(self.hwnd, SW_SHOW)
            user32.SetForegroundWindow(self.hwnd)

    def end(self):
        """结束焦点会话：隐藏替身窗口，前台由用户下一次点击决定归属。"""
        if self.hwnd:
            user32.ShowWindow(self.hwnd, SW_HIDE)

    def destroy(self):
        """销毁替身窗口，在应用退出时调用。"""
        self.end()
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None
