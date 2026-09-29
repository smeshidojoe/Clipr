"""Icons drawn at runtime, so the app ships without binary assets."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)

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


def app_pixmap(size: int, recording: bool = False) -> QPixmap:
    """Знак программы в физических пикселях (dpr задаёт вызывающий)."""
    return _pixmap(size, recording)


def app_icon(recording: bool = False) -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_pixmap(size, recording))
    return icon


# --- пиктограммы интерфейса ------------------------------------------------ #
# Раньше кнопки панели подписывались символами из шрифта (⬚ ❚❚ ■ 📁 ⚙ ✕): разная
# насыщенность, разный кегль, а «папка» вообще приезжала цветной эмодзи. Рисуем
# сами — одна толщина штриха, одна сетка, один цвет.

_GRID = 24.0          # все контуры описаны в сетке 24×24
_STROKE = 2.0


def _pen(colour: QColor, width: float = _STROKE) -> QPen:
    pen = QPen(colour, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _draw_glyph(painter: QPainter, name: str, colour: QColor) -> None:
    painter.setPen(_pen(colour))
    painter.setBrush(Qt.NoBrush)

    if name == "region":
        # Уголки рамки захвата — тот же мотив, что у подсветки области.
        arm, inset = 6.0, 4.0
        far = _GRID - inset
        for x, y, dx, dy in ((inset, inset, 1, 1), (far, inset, -1, 1),
                             (inset, far, 1, -1), (far, far, -1, -1)):
            painter.drawLine(QPointF(x, y), QPointF(x + dx * arm, y))
            painter.drawLine(QPointF(x, y), QPointF(x, y + dy * arm))
    elif name == "record":
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawEllipse(QPointF(12, 12), 6, 6)
    elif name == "pause":
        painter.setPen(_pen(colour, 3.2))
        painter.drawLine(QPointF(9, 6.5), QPointF(9, 17.5))
        painter.drawLine(QPointF(15, 6.5), QPointF(15, 17.5))
    elif name == "play":
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        path = QPainterPath(QPointF(8.5, 6))
        path.lineTo(18, 12)
        path.lineTo(8.5, 18)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "stop":
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawRoundedRect(QRectF(7, 7, 10, 10), 2.2, 2.2)
    elif name == "folder":
        path = QPainterPath(QPointF(4, 8))
        path.lineTo(4, 18)
        path.lineTo(20, 18)
        path.lineTo(20, 9.5)
        path.lineTo(12, 9.5)
        path.lineTo(10, 7)
        path.lineTo(4, 7)
        path.closeSubpath()
        painter.drawPath(path)
    elif name == "settings":
        # Ползунки читаются на 18 px лучше, чем шестерёнка: у шестерёнки на
        # такой сетке зубцы сливаются в кашу.
        for y, knob in ((8.5, 15.0), (15.5, 9.5)):
            painter.drawLine(QPointF(4.5, y), QPointF(19.5, y))
            painter.setBrush(colour)
            painter.drawEllipse(QPointF(knob, y), 2.4, 2.4)
            painter.setBrush(Qt.NoBrush)
    elif name == "close":
        painter.drawLine(QPointF(7.5, 7.5), QPointF(16.5, 16.5))
        painter.drawLine(QPointF(16.5, 7.5), QPointF(7.5, 16.5))
    elif name == "film":
        painter.drawRoundedRect(QRectF(4, 5, 16, 14), 2.5, 2.5)
        painter.drawLine(QPointF(8.5, 5), QPointF(8.5, 19))
        painter.drawLine(QPointF(15.5, 5), QPointF(15.5, 19))
        painter.drawLine(QPointF(4, 12), QPointF(8.5, 12))
        painter.drawLine(QPointF(15.5, 12), QPointF(20, 12))
    elif name == "keyboard":
        painter.drawRoundedRect(QRectF(3, 6.5, 18, 11), 2.5, 2.5)
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        for x in (7, 10.3, 13.7, 17):
            painter.drawEllipse(QPointF(x, 10), 1.05, 1.05)
        painter.setPen(_pen(colour))
        painter.drawLine(QPointF(8.5, 14), QPointF(15.5, 14))
    elif name == "power":
        path = QPainterPath()
        # Кольцо с разрывом сверху: от 120° против часовой на 300°.
        path.arcMoveTo(QRectF(5, 5.5, 14, 14), 120)
        path.arcTo(QRectF(5, 5.5, 14, 14), 120, 300)
        painter.drawPath(path)
        painter.drawLine(QPointF(12, 4), QPointF(12, 11.5))
    elif name == "info":
        painter.drawEllipse(QPointF(12, 12), 8, 8)
        painter.drawLine(QPointF(12, 11), QPointF(12, 16))
        painter.setPen(Qt.NoPen)
        painter.setBrush(colour)
        painter.drawEllipse(QPointF(12, 8), 1.2, 1.2)
    elif name == "check":
        painter.drawLine(QPointF(6, 12.5), QPointF(10.5, 17))
        painter.drawLine(QPointF(10.5, 17), QPointF(18, 7.5))


def glyph_pixmap(name: str, size: int = 18, colour: str = theme.TEXT,
                 ratio: float = 2.0) -> QPixmap:
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    # У пиксмапа задан devicePixelRatio, поэтому painter работает в логических
    # координатах: масштабируем сетку 24×24 к логическому размеру, БЕЗ ratio.
    painter.scale(size / _GRID, size / _GRID)
    _draw_glyph(painter, name, QColor(colour))
    painter.end()
    return pixmap


def glyph(name: str, size: int = 18, colour: str = theme.TEXT) -> QIcon:
    """Пиктограмма для кнопки. Имена: region, record, pause, play, stop,
    folder, settings, close, check, film, keyboard, power, info."""
    icon = QIcon()
    icon.addPixmap(glyph_pixmap(name, size, colour))
    icon.addPixmap(glyph_pixmap(name, size, theme.TEXT_DIM), QIcon.Disabled)
    return icon
