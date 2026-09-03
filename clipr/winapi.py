"""Thin ctypes wrappers over the Win32 bits Qt does not expose.

Two things matter here:

* ``exclude_from_capture`` — WDA_EXCLUDEFROMCAPTURE keeps our own overlay out of
  the recording. Since Windows 10 2004 this affinity hides the window from
  BitBlt-based capture (gdigrab) *and* from Desktop Duplication (ddagrab), so
  the panel can stay on screen while recording instead of being hidden.
* ``HotkeyManager`` — RegisterHotKey gives real system-wide hotkeys; Qt
  shortcuts only fire when the app has focus, which is useless for a tray tool.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal
from PySide6.QtGui import QKeySequence

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11
WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000


def exclude_from_capture(win_id: int, enabled: bool = True) -> bool:
    """Hide/unhide a window from screen capture. Returns False if unsupported."""
    if not win_id:
        return False
    affinity = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
    return bool(user32.SetWindowDisplayAffinity(wintypes.HWND(win_id), affinity))


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM
)


def monitor_rects() -> dict[str, tuple[int, int, int, int]]:
    """Physical-pixel rects per display device, keyed by name (``\\\\.\\DISPLAY1``).

    Qt reports screen geometry in scaled logical coordinates; FFmpeg's capture
    offsets are physical. ``QScreen.name()`` matches ``szDevice``, which gives an
    exact mapping between the two even on mixed-DPI setups.
    """
    found: dict[str, tuple[int, int, int, int]] = {}

    def callback(hmonitor, _hdc, _rect, _param):
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(_MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            r = info.rcMonitor
            found[info.szDevice] = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        return True

    user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(callback), 0)
    return found


_QT_TO_VK = {
    "SPACE": 0x20,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "TAB": 0x09,
    "RETURN": 0x0D,
    "ENTER": 0x0D,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PGUP": 0x21,
    "PGDOWN": 0x22,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "PRINT": 0x2C,
    "PRTSC": 0x2C,
}
for _i in range(1, 25):
    _QT_TO_VK[f"F{_i}"] = 0x6F + _i


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """"Ctrl+Alt+R" -> (modifiers, virtual-key). None when unparsable."""
    seq = QKeySequence.fromString(text.strip(), QKeySequence.PortableText)
    if seq.isEmpty():
        return None
    parts = [p.strip().upper() for p in seq.toString(QKeySequence.PortableText).split("+")]
    key = parts[-1]
    mods = 0
    for part in parts[:-1]:
        mods |= {
            "CTRL": MOD_CONTROL,
            "ALT": MOD_ALT,
            "SHIFT": MOD_SHIFT,
            "META": MOD_WIN,
        }.get(part, 0)
    if key in _QT_TO_VK:
        vk = _QT_TO_VK[key]
    elif len(key) == 1:
        vk = ord(key)
    else:
        return None
    return mods | MOD_NOREPEAT, vk


class HotkeyManager(QObject, QAbstractNativeEventFilter):
    """System-wide hotkeys via RegisterHotKey; emits ``triggered(name)``."""

    triggered = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)
        self._by_id: dict[int, str] = {}
        self._next_id = 0xC000

    def register(self, name: str, hotkey: str) -> bool:
        parsed = parse_hotkey(hotkey)
        if parsed is None:
            return False
        mods, vk = parsed
        hotkey_id = self._next_id
        if not user32.RegisterHotKey(None, hotkey_id, mods, vk):
            return False
        self._by_id[hotkey_id] = name
        self._next_id += 1
        return True

    def unregister_all(self) -> None:
        for hotkey_id in list(self._by_id):
            user32.UnregisterHotKey(None, hotkey_id)
        self._by_id.clear()
        self._next_id = 0xC000

    def nativeEventFilter(self, event_type, message):  # noqa: N802 (Qt signature)
        if event_type == b"windows_generic_MSG":
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == WM_HOTKEY:
                name = self._by_id.get(msg.wParam)
                if name:
                    self.triggered.emit(name)
        return False, 0


def round_window_corners(win_id: int, width: int, height: int, radius: int = 18) -> bool:
    """Скругляет окно вырезанием региона.

    Обычный путь к скруглению в Qt — WA_TranslucentBackground, но он делает окно
    WS_EX_LAYERED, а на layered-окне SetWindowDisplayAffinity падает с
    ERROR_NOT_ENOUGH_MEMORY (8) — то есть панель попадала бы в собственную
    запись. Регион даёт скругление без слоя.
    """
    if not win_id or width <= 0 or height <= 0:
        return False
    region = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius * 2, radius * 2)
    if not region:
        return False
    # SetWindowRgn забирает регион себе — удалять его нам не нужно.
    return bool(user32.SetWindowRgn(wintypes.HWND(win_id), region, True))


SPI_GETCLIENTAREAANIMATION = 0x1042


def system_animations_enabled() -> bool:
    """Системная настройка «Показывать анимацию в Windows».

    Аналог ``prefers-reduced-motion`` в вебе: если пользователь отключил
    анимацию в параметрах доступности, интерфейс обязан это уважать, а не ждать
    отдельной галочки внутри программы.
    """
    value = wintypes.BOOL()
    ok = user32.SystemParametersInfoW(
        SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0)
    return bool(value.value) if ok else True
