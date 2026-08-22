"""Генерация ассетов: многоразмерная иконка exe и баннер-карточка.

Иконка рисуется тем же кодом, что и иконка в трее (clipr/ui/icons.py), поэтому
приложение, трей и exe всегда выглядят одинаково — отдельной картинки, которая
однажды разойдётся с интерфейсом, здесь нет.

Запуск: python tools_gen_assets.py
"""

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import QApplication

from clipr.ui import theme
from clipr.ui.icons import _pixmap

ASSETS = Path(__file__).parent / "assets"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _png_bytes(pixmap: QPixmap) -> bytes:
    # QBuffer держит указатель на QByteArray — временный объект тут собрал бы
    # сборщик мусора прямо под руками Qt.
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QBuffer.WriteOnly)
    pixmap.save(buffer, "PNG")
    buffer.close()
    return bytes(storage)


def write_ico(path: Path) -> None:
    """Собираем ICO вручную: Qt сохраняет только один размер, а Windows берёт
    из иконки exe разные — 16 px для заголовка окна, 256 px для крупной плитки.
    """
    images = [_png_bytes(_pixmap(size, False)) for size in ICO_SIZES]
    offset = 6 + 16 * len(images)
    header = struct.pack("<HHH", 0, 1, len(images))
    entries, blobs = b"", b""
    for size, data in zip(ICO_SIZES, images):
        entries += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    path.write_bytes(header + entries + blobs)


def _font(size: int, weight: QFont.Weight, tracking: float = 0.0) -> QFont:
    font = QFont("Segoe UI Variable Display")
    if not font.exactMatch():
        font = QFont("Segoe UI")
    font.setPixelSize(size)
    font.setWeight(weight)
    font.setLetterSpacing(QFont.AbsoluteSpacing, tracking)
    return font


def _glow(painter, centre, radius, alpha):
    """Мягкое свечение.

    Радиальный градиент из двух-трёх стопов на 8 битах даёт видимые кольца —
    поэтому спад разбит на много ступеней по квадратичной кривой.
    """
    gradient = QRadialGradient(centre, radius)
    steps = 24
    for i in range(steps + 1):
        t = i / steps
        gradient.setColorAt(t, QColor(245, 98, 61, int(alpha * (1.0 - t) ** 2.2)))
    painter.fillRect(QRectF(centre.x() - radius, centre.y() - radius,
                            radius * 2, radius * 2), gradient)


def _noise(painter, width, height, alpha=7):
    """Тонкое зерно поверх градиента: добивает остатки полос и даёт фактуру."""
    import random
    rng = random.Random(20260822)
    painter.save()
    for _ in range(width * height // 90):
        painter.setPen(QColor(255, 255, 255, rng.randint(1, alpha)))
        painter.drawPoint(rng.randrange(width), rng.randrange(height))
    painter.restore()


def _corner_brackets(painter, rect, colour, length=54, thickness=3):
    """Уголки рамки захвата — тот же мотив, что и подсветка выбранной области."""
    painter.setPen(Qt.NoPen)
    painter.setBrush(colour)
    for x, y, dx, dy in (
        (rect.left(), rect.top(), 1, 1),
        (rect.right(), rect.top(), -1, 1),
        (rect.left(), rect.bottom(), 1, -1),
        (rect.right(), rect.bottom(), -1, -1),
    ):
        painter.drawRect(QRectF(min(x, x + dx * length), min(y, y + dy * thickness),
                                length, thickness))
        painter.drawRect(QRectF(min(x, x + dx * thickness), min(y, y + dy * length),
                                thickness, length))


def write_banner(path: Path, width: int = 1280, height: int = 720) -> None:
    """Карточка-баннер в стиле приложения: тёмный фон, тёплое свечение под
    иконкой, крупное имя с отрицательным трекингом (как в theme.py)."""
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(theme.WINDOW))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)

    backdrop = QLinearGradient(0, 0, 0, height)
    backdrop.setColorAt(0.0, QColor("#1B1B22"))
    backdrop.setColorAt(0.62, QColor("#141418"))
    backdrop.setColorAt(1.0, QColor("#0E0E12"))
    painter.fillRect(0, 0, width, height, backdrop)

    centre = QPointF(width / 2, height * 0.35)
    _glow(painter, centre, width * 0.46, 70)

    # Сетка точек — фактура, которая не спорит с текстом.
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 255, 255, 10))
    for x in range(40, width, 40):
        for y in range(40, height, 40):
            painter.drawEllipse(QPointF(x, y), 1.1, 1.1)

    _corner_brackets(painter, QRectF(48, 48, width - 96, height - 96),
                     QColor(245, 98, 61, 150))

    # Иконка приложения — та же, что в трее.
    icon_size = 168
    icon = _pixmap(icon_size * 2, False).scaled(
        icon_size, icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    icon_rect = QRectF(centre.x() - icon_size / 2, centre.y() - icon_size / 2 - 34,
                       icon_size, icon_size)
    painter.drawPixmap(icon_rect.toRect(), icon)

    # Имя: крупный кегль — отрицательный трекинг, иначе буквы «разъезжаются».
    painter.setPen(QColor(theme.TEXT))
    painter.setFont(_font(116, QFont.DemiBold, -3.4))
    name_rect = QRectF(0, icon_rect.bottom() + 26, width, 140)
    painter.drawText(name_rect, Qt.AlignHCenter | Qt.AlignTop, "Clipr")

    # Подпись: мелкий кегль — наоборот, чуть разреженный.
    painter.setPen(QColor(theme.TEXT_DIM))
    painter.setFont(_font(30, QFont.Normal, 0.4))
    painter.drawText(QRectF(0, name_rect.top() + 152, width, 48),
                     Qt.AlignHCenter | Qt.AlignTop, "Запись экрана в лёгкий GIF")

    # Нижняя строка-«пилюли».
    painter.setFont(_font(20, QFont.Medium, 0.6))
    labels = ["Обрезка и кадрирование", "Палитра в два прохода", "Живёт в трее"]
    metrics = painter.fontMetrics()
    pad, gap = 20, 14
    widths = [metrics.horizontalAdvance(text) + pad * 2 for text in labels]
    x = (width - sum(widths) - gap * (len(labels) - 1)) / 2
    y = height - 132
    for text, pill_width in zip(labels, widths):
        rect = QRectF(x, y, pill_width, 44)
        pill = QPainterPath()
        pill.addRoundedRect(rect, 22, 22)
        painter.fillPath(pill, QColor(255, 255, 255, 16))
        painter.setPen(QColor(255, 255, 255, 32))
        painter.setBrush(Qt.NoBrush)      # иначе «пилюли» зальются кистью от уголков
        painter.drawPath(pill)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(rect, Qt.AlignCenter, text)
        x += pill_width + gap

    _noise(painter, width, height)
    painter.end()
    pixmap.save(str(path), "PNG")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    ASSETS.mkdir(exist_ok=True)
    write_ico(ASSETS / "app.ico")
    write_banner(ASSETS / "banner-1280x720.png")
    _pixmap(512, False).save(str(ASSETS / "app.png"), "PNG")
    print("готово:", *(p.name for p in sorted(ASSETS.iterdir())))
