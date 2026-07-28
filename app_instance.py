"""Multi-instance window helpers for the desktop application.

The Exporter intentionally does not use a process mutex: every executable
launch owns its own Python interpreter, Tk root, workers, and export state.
On Windows, however, a newly launched root can open directly behind an
existing centred root. That looks like a single-instance application even
though a second process and window are alive.
"""
from __future__ import annotations

import os
import platform
from typing import Iterable, Optional


APP_WINDOW_TITLES = (
    "Tranzor Translation Exporter",
    "Tranzor 翻译导出器",
)
CASCADE_STEP_PX = 32
CASCADE_SLOTS = 6
TASKBAR_APP_ID_BASE = "Tranzor.TranslationExporter"


def taskbar_app_id(pid: Optional[int] = None) -> str:
    """Per-process AppUserModelID: unique PID suffix keeps instances apart."""
    return f"{TASKBAR_APP_ID_BASE}.{int(pid if pid is not None else os.getpid())}"


def ungroup_taskbar_icon(
    *,
    platform_name: Optional[str] = None,
    pid: Optional[int] = None,
) -> Optional[str]:
    """Give this process its own taskbar button on Windows.

    The taskbar groups windows by AppUserModelID (falling back to the exe
    path), so every instance of the same executable collapses into one
    icon. Assigning a PID-unique ID before the first window is shown makes
    Windows treat each instance as a separate application, so the icons
    tile instead of stacking. Returns the ID that was set, or ``None`` when
    not on Windows or the shell call failed (grouping then stays as-is).
    """
    system = platform_name or platform.system()
    if system != "Windows":
        return None
    app_id = taskbar_app_id(pid)
    try:
        import ctypes

        hresult = ctypes.WinDLL("shell32").SetCurrentProcessExplicitAppUserModelID(
            ctypes.c_wchar_p(app_id)
        )
        if hresult != 0:
            return None
    except Exception:
        return None
    return app_id


def _is_app_main_window_title(title: str) -> bool:
    """Return whether *title* belongs to an Exporter root window."""
    text = str(title or "")
    return any(
        text == base or text.startswith(base + " · ")
        for base in APP_WINDOW_TITLES
    )


def _visible_window_titles_windows() -> list[str]:
    """Enumerate visible top-level Win32 window titles, best-effort."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
    user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        wintypes.HWND,
        wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetWindowTextW.restype = ctypes.c_int

    titles: list[str] = []

    @enum_proc_type
    def _visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        if user32.GetWindowTextW(hwnd, buffer, length + 1):
            titles.append(buffer.value)
        return True

    if not user32.EnumWindows(_visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return titles


def count_existing_instances(
    *,
    titles: Optional[Iterable[str]] = None,
    platform_name: Optional[str] = None,
) -> int:
    """Count visible Exporter roots that existed before this root is shown."""
    system = platform_name or platform.system()
    if titles is None:
        if system != "Windows":
            return 0
        try:
            titles = _visible_window_titles_windows()
        except Exception:
            return 0
    return sum(1 for title in titles if _is_app_main_window_title(title))


def cascade_position(
    screen_width: int,
    screen_height: int,
    window_width: int,
    window_height: int,
    existing_count: int,
) -> tuple[int, int]:
    """Return a centred, screen-bounded cascade position."""
    sw = max(0, int(screen_width))
    sh = max(0, int(screen_height))
    ww = max(0, int(window_width))
    wh = max(0, int(window_height))
    max_x = max(0, sw - ww)
    max_y = max(0, sh - wh)
    centre_x = max_x // 2
    centre_y = max_y // 2
    slot = max(0, int(existing_count)) % CASCADE_SLOTS
    shift = slot * CASCADE_STEP_PX
    return min(max_x, centre_x + shift), min(max_y, centre_y + shift)


def surface_new_instance(
    root,
    existing_count: int,
    *,
    platform_name: Optional[str] = None,
) -> bool:
    """Briefly raise a newly deiconified second-or-later Windows root."""
    system = platform_name or platform.system()
    if system != "Windows" or int(existing_count or 0) <= 0:
        return False

    def _clear_topmost():
        try:
            if root.winfo_exists():
                root.attributes("-topmost", False)
        except Exception:
            pass

    def _activate():
        try:
            root.lift()
        except Exception:
            pass
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        try:
            root.focus_force()
        except Exception:
            pass
        try:
            root.after(450, _clear_topmost)
        except Exception:
            _clear_topmost()

    try:
        root.after(50, _activate)
    except Exception:
        _activate()
    return True
