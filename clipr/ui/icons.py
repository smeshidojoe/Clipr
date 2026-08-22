"""Icons drawn at runtime, so the app ships without binary assets."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from . import theme


def _pixmap(size: int, recording: bool) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    body = QRectF(size * 0.06, size * 0.06, size * 0.88, size * 0.88)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(theme.ACCENT) if recording else QColor("#26262C"))
    painter.drawRoundedRect(body, size * 0.26, size * 0.26)

    painter.setBrush(QColor("#FFFFFF") if recording else QColor(theme.ACCENT))
    radius = size * 0.19
    painter.drawEllipse(body.center(), radius, radius)
    painter.end()
    return pixmap


def app_icon(recording: bool = False) -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_pixmap(size, recording))
    return icon
