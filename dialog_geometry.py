"""Size editor dialogs within their parent's monitor, including native frames."""
import sys


def window_rect(widget):
    if sys.platform == "win32":
        import ctypes as c
        from ctypes import wintypes as w
        user = c.windll.user32
        user.GetAncestor.argtypes = [w.HWND, w.UINT]
        user.GetAncestor.restype = w.HWND
        user.GetWindowRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
        user.GetWindowRect.restype = w.BOOL
        hwnd = user.GetAncestor(widget.winfo_id(), 2)
        rect = w.RECT()
        if hwnd and user.GetWindowRect(hwnd, c.byref(rect)):
            return rect.left, rect.top, rect.right, rect.bottom
    x, y = widget.winfo_rootx(), widget.winfo_rooty()
    return x, y, x + widget.winfo_width(), y + widget.winfo_height()


def work_area(widget):
    if sys.platform == "win32":
        import ctypes as c
        from ctypes import wintypes as w
        class MonitorInfo(c.Structure):
            _fields_ = [("size", w.DWORD), ("monitor", w.RECT), ("work", w.RECT), ("flags", w.DWORD)]
        user = c.windll.user32
        user.MonitorFromWindow.argtypes = [w.HWND, w.DWORD]
        user.MonitorFromWindow.restype = w.HANDLE
        monitor = user.MonitorFromWindow(widget.winfo_id(), 2)
        user.GetMonitorInfoW.argtypes = [w.HANDLE, c.POINTER(MonitorInfo)]
        info = MonitorInfo()
        info.size = c.sizeof(info)
        if monitor and user.GetMonitorInfoW(monitor, c.byref(info)):
            return info.work.left, info.work.top, info.work.right, info.work.bottom
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def center_dialog(dialog, parent):
    """Center the native outer frame on its owner, bounded by that monitor."""
    parent.update_idletasks()
    dialog.update_idletasks()
    left, top, right, bottom = work_area(parent)
    p, frame = window_rect(parent), window_rect(dialog)
    width, height = dialog.winfo_width(), dialog.winfo_height()
    border_x = max(0, frame[2] - frame[0] - width)
    border_y = max(0, frame[3] - frame[1] - height)
    width = max(1, min(width, right - left - border_x - 24))
    height = max(1, min(height, bottom - top - border_y - 24))
    minimum = dialog.minsize()
    dialog.minsize(min(minimum[0], width), min(minimum[1], height))
    outer_width, outer_height = width + border_x, height + border_y
    x = round((p[0] + p[2] - outer_width) / 2)
    y = round((p[1] + p[3] - outer_height) / 2)
    x = max(left + 12, min(x, right - outer_width - 12))
    y = max(top + 12, min(y, bottom - outer_height - 12))
    dialog.geometry(f"{width}x{height}+{x}+{y}")
    return width, height, x, y


def place_large_dialog(dialog, parent):
    parent.update_idletasks()
    left, top, right, bottom = work_area(parent)
    width = min(max(1480, round(parent.winfo_width() * .96)), right - left - 64)
    height = min(max(880, round(parent.winfo_height() * .94)), bottom - top - 96)
    width, height = max(1, width), max(1, height)
    dialog.minsize(min(1000, width), min(720, height))
    dialog.geometry(f"{width}x{height}")
    # A leading '+' in center_dialog preserves negative desktop coordinates.
    return center_dialog(dialog, parent)
