"""Подсветка выбранной области записи.

Рамка собрана из четырёх тонких НЕПРОЗРАЧНЫХ полосок-окон, а не из одного окна
с дыркой посередине. Причина та же, что и у панели: окно с
``WA_TranslucentBackground`` становится layered, а на layered-окне
``SetWindowDisplayAffinity`` отказывает — рамка попала бы в собственную запись.

Полоски стоят СНАРУЖИ области, поэтому не закрывают ни пикселя из того, что
пишется, и не принимают клики (``WindowTransparentForInput``).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRect, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel, QWidget

from ..winapi import exclude_from_capture
from . import theme

THICKNESS = 2
IDLE_COLOR = QColor(theme.ACCENT)
RECORDING_COLOR = QColor(theme.DANGER)


class _Strip(QWidget):
    """Одна сторона рамки: сплошная полоска, не ловящая мышь."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._colour = IDLE_COLOR

    def set_colour(self, colour: QColor) -> None:
        self._colour = colour
        self.update()

    def paintEvent(self, _event) -> None:
        QPainter(self).fillRect(self.rect(), self._colour)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        exclude_from_capture(int(self.winId()), True)


class _Badge(QLabel):
    """Размер области — подпись у угла рамки."""

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            f"background: {theme.SURFACE}; color: {theme.TEXT};"
            f"font-size: 11px; padding: 3px 8px;"
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        exclude_from_capture(int(self.winId()), True)


class RegionFrame(QObject):
    """Рамка вокруг выбранной области. Координаты — логические (как у Qt)."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._strips = [_Strip() for _ in range(4)]
        self._badge = _Badge()
        self._rect: QRect | None = None

    # ---- управление ------------------------------------------------------
    def show_region(self, rect: QRect, label: str = "") -> None:
        self._rect = QRect(rect)
        thickness = THICKNESS
        top, bottom, left, right = self._strips
        top.setGeometry(rect.x() - thickness, rect.y() - thickness,
                        rect.width() + thickness * 2, thickness)
        bottom.setGeometry(rect.x() - thickness, rect.bottom() + 1,
                           rect.width() + thickness * 2, thickness)
        left.setGeometry(rect.x() - thickness, rect.y(), thickness, rect.height())
        right.setGeometry(rect.right() + 1, rect.y(), thickness, rect.height())
        for strip in self._strips:
            strip.show()
            strip.raise_()

        self._badge.setText(label or f"{rect.width()}×{rect.height()}")
        self._badge.adjustSize()
        # Бейдж над рамкой, а если сверху нет места — под ней.
        badge_y = rect.y() - thickness - self._badge.height() - 4
        if badge_y < 0:
            badge_y = rect.bottom() + thickness + 4
        self._badge.move(rect.x() - thickness, badge_y)
        self._badge.show()
        self._badge.raise_()

    def set_recording(self, recording: bool) -> None:
        colour = RECORDING_COLOR if recording else IDLE_COLOR
        for strip in self._strips:
            strip.set_colour(colour)

    def hide(self) -> None:
        for strip in self._strips:
            strip.hide()
        self._badge.hide()

    @property
    def is_visible(self) -> bool:
        return self._strips[0].isVisible()
