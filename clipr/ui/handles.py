"""Рамка, которую правят за края и углы: общее для кадрирования в редакторе
и для выбора области записи."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QPainter

EDGE_CURSORS = {
    "tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor,
    "tr": Qt.SizeBDiagCursor, "bl": Qt.SizeBDiagCursor,
    "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
    "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor,
    "move": Qt.SizeAllCursor,
}


def edge_hit(rect: QRectF, pos: QPointF, grab: float) -> str | None:
    """Сторона рамки под точкой: углы важнее краёв, края важнее середины."""
    near_l = abs(pos.x() - rect.left()) <= grab
    near_r = abs(pos.x() - rect.right()) <= grab
    near_t = abs(pos.y() - rect.top()) <= grab
    near_b = abs(pos.y() - rect.bottom()) <= grab
    inside_x = rect.left() - grab <= pos.x() <= rect.right() + grab
    inside_y = rect.top() - grab <= pos.y() <= rect.bottom() + grab
    vertical = "t" if near_t else "b" if near_b else ""
    horizontal = "l" if near_l else "r" if near_r else ""
    if vertical and horizontal:
        return vertical + horizontal
    if horizontal and inside_y:
        return horizontal
    if vertical and inside_x:
        return vertical
    return "move" if rect.contains(pos) else None


def drag_sides(rect: QRectF, sides: str, point: QPointF) -> QRectF:
    """Рамка, у которой стороны из `sides` ("tl", "r"…) дотянуты до точки."""
    rect = QRectF(rect)
    if "l" in sides:
        rect.setLeft(point.x())
    if "r" in sides:
        rect.setRight(point.x())
    if "t" in sides:
        rect.setTop(point.y())
    if "b" in sides:
        rect.setBottom(point.y())
    return rect.normalized()


def draw_edge_grips(painter: QPainter, rect: QRectF) -> None:
    """Короткие планки посередине сторон — подсказка, что край тянется сам
    по себе, а не только за углы. Цвет и кисть задаёт вызывающий."""
    centre = rect.center()
    for x, y, horizontal in ((centre.x(), rect.top(), True), (centre.x(), rect.bottom(), True),
                             (rect.left(), centre.y(), False), (rect.right(), centre.y(), False)):
        length = min(14.0, (rect.width() if horizontal else rect.height()) / 4)
        if length < 6:
            continue
        grip = QRectF(x - length / 2, y - 2, length, 4) if horizontal \
            else QRectF(x - 2, y - length / 2, 4, length)
        painter.drawRoundedRect(grip, 2, 2)
