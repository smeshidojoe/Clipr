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


# --- выходы Desktop Duplication ---------------------------------------------- #

class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16),
                ("d", ctypes.c_ubyte * 8)]


class _DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [("DeviceName", wintypes.WCHAR * 32), ("DesktopCoordinates", wintypes.RECT),
                ("AttachedToDesktop", wintypes.BOOL), ("Rotation", ctypes.c_int),
                ("Monitor", wintypes.HMONITOR)]


def _com(obj, index: int, *argtypes):
    """Метод COM-объекта по номеру в vtable. HRESULT-ошибка — OSError."""
    vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])


def dda_outputs() -> list[tuple[int, int, int, int]]:
    """Прямоугольники выходов основного видеоадаптера в порядке DXGI:
    индекс в списке — это `output_idx` фильтра ddagrab.

    ddagrab нумерует мониторы по DXGI, а не по EnumDisplayMonitors, и смещения
    ждёт относительно своего монитора. Координаты здесь те же физические, что
    у Region, поэтому нужный выход находится простым попаданием в прямоугольник.
    ffmpeg создаёт устройство на адаптере по умолчанию — берём его же (0).
    """
    import uuid
    iid = uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387")      # IDXGIFactory1
    guid = _GUID(iid.fields[0], iid.fields[1], iid.fields[2])
    guid.d[:] = iid.bytes[8:]
    found: list[tuple[int, int, int, int]] = []
    factory = adapter = None
    try:
        dxgi = ctypes.WinDLL("dxgi")
        factory = ctypes.c_void_p()
        if dxgi.CreateDXGIFactory1(ctypes.byref(guid), ctypes.byref(factory)) != 0:
            return found
        adapter = ctypes.c_void_p()
        # IDXGIFactory::EnumAdapters (7), IDXGIAdapter::EnumOutputs (7),
        # IDXGIOutput::GetDesc (7), IUnknown::Release (2).
        _com(factory, 7, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
            factory, 0, ctypes.byref(adapter))
        index = 0
        while True:
            output = ctypes.c_void_p()
            try:
                _com(adapter, 7, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p))(
                    adapter, index, ctypes.byref(output))
            except OSError:                      # DXGI_ERROR_NOT_FOUND — выходы кончились
                break
            desc = _DXGI_OUTPUT_DESC()
            _com(output, 7, ctypes.POINTER(_DXGI_OUTPUT_DESC))(output, ctypes.byref(desc))
            rect = desc.DesktopCoordinates
            found.append((rect.left, rect.top, rect.right, rect.bottom))
            _com(output, 2)(output)
            index += 1
    except OSError:
        pass
    finally:
        for obj in (adapter, factory):
            if obj:
                _com(obj, 2)(obj)
    return found
