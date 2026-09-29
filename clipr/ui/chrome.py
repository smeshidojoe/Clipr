"""Рамка окна без системного заголовка (по образцу Scribe/Dictify).

WM_NCCALCSIZE убирает системный заголовок, но окно остаётся обычным окном с
рамкой — поэтому Windows по-прежнему даёт тень, Aero Snap, анимации
сворачивания и изменение размера за края. WM_NCHITTEST отдаёт края под
изменение размера, а пустое место полосы заголовка — под «заголовок» (таскать
окно, системное меню по Alt+Space). Кнопки заголовка — свои.

Почему не FramelessWindowHint: с ним пропадают тень и привязка к краям, и
окно выглядит наклейкой поверх экрана, а не окном Windows.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QComboBox, QLabel, QLineEdit, QWidget,
)

from . import motion, theme
from .controls import Tween

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
HTCLIENT, HTCAPTION = 1, 2
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = (
    10, 11, 12, 13, 14, 15, 16, 17)
SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 32, 33, 92
SWP_FRAMECHANGED = 0x0020
SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0002, 0x0001, 0x0004, 0x0010

CAPTION_HEIGHT = 44


def _is_drag_area(widget: QWidget | None, bar: QWidget) -> bool:
    """Пустое место полосы и простые подписи таскают окно; элементы управления
    остаются кликабельными."""
    if widget is None or widget is bar:
        return True
    if isinstance(widget, (QAbstractButton, QLineEdit, QComboBox, QAbstractSpinBox)):
        return False
    if isinstance(widget, QLabel) and not (
            widget.textInteractionFlags() & Qt.TextSelectableByMouse):
        return bar.isAncestorOf(widget)
    return False


class CaptionButton(QAbstractButton):
    """Кнопка заголовка в духе Windows 11: подсветка проявляется, а не
    вспыхивает; «закрыть» краснеет."""

    def __init__(self, kind: str, parent=None) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedSize(46, CAPTION_HEIGHT)
        self._hover = Tween(self, 0.0, motion.HOVER_MS, motion.EASE)

    def enterEvent(self, event) -> None:
        self._hover.to(1.0)

    def leaveEvent(self, event) -> None:
        self._hover.to(0.0)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        hover = self._hover.value
        pressed = self.isDown()
        if self.kind == "close":
            if hover or pressed:
                colour = QColor("#C42B1C" if pressed else "#E81123")
                colour.setAlphaF(1.0 if pressed else hover)
                painter.fillRect(self.rect(), colour)
            glyph = theme.mix(theme.TEXT, "#FFFFFF", hover)
        else:
            if hover or pressed:
                painter.fillRect(self.rect(), QColor(255, 255, 255,
                                                     int(12 if pressed else 20 * hover)))
            glyph = QColor(theme.TEXT)
        painter.setRenderHint(QPainter.Antialiasing, self.kind == "close")
        painter.setPen(QPen(glyph, 1.0))
        centre = QPointF(self.width() / 2, self.height() / 2)
        size = 5.0
        if self.kind == "min":
            painter.drawLine(QPointF(centre.x() - size, centre.y()),
                             QPointF(centre.x() + size, centre.y()))
        else:
            painter.drawLine(QPointF(centre.x() - size, centre.y() - size),
                             QPointF(centre.x() + size, centre.y() + size))
            painter.drawLine(QPointF(centre.x() + size, centre.y() - size),
                             QPointF(centre.x() - size, centre.y() + size))


class _MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int),
                ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class WindowsChrome:
    """Подключается к окну: `window.nativeEvent` зовёт `native_event`,
    `window.showEvent` — `on_show`. `bar` — полоса-заголовок."""

    def __init__(self, window: QWidget, bar: QWidget, bar_layout) -> None:
        self.window = window
        self.bar = bar
        self._enabled = QGuiApplication.platformName() == "windows"
        self._shown = False

        user32 = self._user32 = ctypes.WinDLL("user32")
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = ctypes.c_uint
        user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, ctypes.c_uint]
        user32.IsZoomed.argtypes = [wintypes.HWND]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 \
            + [ctypes.c_uint]
        self._dwm = ctypes.WinDLL("dwmapi")
        self._dwm.DwmExtendFrameIntoClientArea.argtypes = [
            wintypes.HWND, ctypes.POINTER(_MARGINS)]

        bar.setFixedHeight(CAPTION_HEIGHT)
        self.buttons = {}
        if not self._enabled:
            return                         # offscreen в тестах: обычная рамка
        for kind in ("min", "close"):
            button = CaptionButton(kind)
            bar_layout.addWidget(button, 0, Qt.AlignTop)
            self.buttons[kind] = button
        self.buttons["min"].clicked.connect(window.showMinimized)
        self.buttons["close"].clicked.connect(window.close)

    def on_show(self) -> None:
        if self._shown or not self._enabled:
            return
        self._shown = True
        hwnd = int(self.window.winId())
        # Тень DWM остаётся, хотя клиентская область теперь занимает всё окно.
        self._dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_MARGINS(0, 0, 1, 0)))
        self._user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)

    def _frame(self, hwnd: int) -> tuple[int, int]:
        dpi = self._user32.GetDpiForWindow(hwnd) or 96
        metric = self._user32.GetSystemMetricsForDpi
        pad = metric(SM_CXPADDEDBORDER, dpi)
        return metric(SM_CXSIZEFRAME, dpi) + pad, metric(SM_CYSIZEFRAME, dpi) + pad

    def native_event(self, event_type, message):
        if not self._enabled or event_type != b"windows_generic_MSG":
            return None
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_NCCALCSIZE and msg.wParam:
            if self._user32.IsZoomed(msg.hWnd):
                # Развёрнутое окно свисает за экран на толщину рамки — возвращаем.
                fx, fy = self._frame(msg.hWnd)
                rect = _NCCALCSIZE_PARAMS.from_address(msg.lParam).rgrc[0]
                rect.left += fx
                rect.right -= fx
                rect.top += fy
                rect.bottom -= fy
            return True, 0
        if msg.message == WM_NCHITTEST:
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            return True, self._hit_test(msg.hWnd, x, y)
        return None

    def _hit_test(self, hwnd: int, x: int, y: int) -> int:
        """x, y — позиция на экране в физических пикселях."""
        rect = wintypes.RECT()
        self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
        lx, ly = x - rect.left, y - rect.top
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if not self._user32.IsZoomed(hwnd):
            fx, fy = self._frame(hwnd)
            left, right = lx < fx, lx >= width - fx
            top, bottom = ly < fy, ly >= height - fy
            for hit, code in (((top and left), HTTOPLEFT), ((top and right), HTTOPRIGHT),
                              ((bottom and left), HTBOTTOMLEFT),
                              ((bottom and right), HTBOTTOMRIGHT),
                              (left, HTLEFT), (right, HTRIGHT),
                              (top, HTTOP), (bottom, HTBOTTOM)):
                if hit:
                    return code
        ratio = self.window.devicePixelRatioF()
        position = QPoint(int(lx / ratio), int(ly / ratio))
        bar_position = self.bar.mapFrom(self.window, position)
        if self.bar.rect().contains(bar_position) and _is_drag_area(
                self.bar.childAt(bar_position), self.bar):
            return HTCAPTION
        return HTCLIENT


__all__ = ["CAPTION_HEIGHT", "CaptionButton", "WindowsChrome"]
