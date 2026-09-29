"""Элементы окна настроек: переключатель, сегменты, карточки со строками,
поле горячей клавиши, тонкая полоса прогресса (по образцу Scribe/Dictify).

Правило движения одно на всё: анимируем только то, что иначе прыгнуло бы
(смена состояния, появление), укладываемся в ~200 мс, каждую анимацию начинаем
с того значения, что сейчас на экране — тогда её можно развернуть посреди пути,
— и никогда не анимируем то, что человек читает. При «меньше анимации» сдвиги
становятся мгновенными, а плавная смена цвета и прозрачности остаётся.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import (
    QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QFontMetricsF, QKeySequence, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QSlider, QStackedWidget,
    QVBoxLayout, QWidget,
)

from ..i18n import tr
from ..winapi import parse_hotkey
from . import motion, theme


def label(text: str = "", name: str = "", wrap: bool = False, parent=None) -> QLabel:
    widget = QLabel(text, parent)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def ui_font(px: float, weight: QFont.Weight = QFont.Normal) -> QFont:
    font = QFont("Segoe UI Variable Text")
    font.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
    font.setPixelSize(round(px))
    font.setWeight(weight)
    return font


# --- анимация ------------------------------------------------------------------ #

class Tween(QVariantAnimation):
    """Одно число, плывущее к цели. Всегда стартует с того, что на экране,
    поэтому смена цели посреди пути разворачивает движение, а не прыгает."""

    def __init__(self, owner: QWidget, value: float = 0.0, duration: int = 180,
                 curve: QEasingCurve | None = None,
                 on_change: Callable[[], None] | None = None) -> None:
        super().__init__(owner)
        self.value = value
        self._owner = owner
        self._on_change = on_change or owner.update
        self.setDuration(duration)
        self.setEasingCurve(curve or motion.EASE_OUT)
        self.valueChanged.connect(self._step)

    def _step(self, value) -> None:
        self.value = float(value)
        self._on_change()

    def to(self, target: float, animate: bool = True) -> None:
        self.stop()
        if not animate or not self._owner.isVisible() or abs(target - self.value) < 1e-4:
            self.value = float(target)
            self._on_change()
            return
        self.setStartValue(self.value)
        self.setEndValue(float(target))
        self.start()


class _Snapshot(QWidget):
    def __init__(self, parent: QWidget, pixmap: QPixmap) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._pixmap = pixmap
        self.tween = Tween(self, 1.0, motion.PAGE_MS, motion.EASE_OUT)
        self.tween.finished.connect(self.deleteLater)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setOpacity(self.tween.value)
        painter.drawPixmap(0, 0, self._pixmap)


def crossfade(stack: QStackedWidget, index: int) -> None:
    """Смена страницы: старая растворяется поверх новой, без резкой склейки.

    Прозрачность остаётся и при «меньше анимации» — это не движение.
    """
    if stack.currentIndex() == index:
        return
    if not stack.isVisible() or stack.currentWidget() is None:
        stack.setCurrentIndex(index)
        return
    cover = _Snapshot(stack, stack.currentWidget().grab())
    cover.setGeometry(stack.rect())
    stack.setCurrentIndex(index)
    cover.show()
    cover.raise_()
    cover.tween.to(0.0)


# --- переключатель ---------------------------------------------------------------- #

class Switch(QWidget):
    """Переключатель в духе iOS: бегунок доезжает до своей стороны и чуть
    расширяется под пальцем, пока кнопка зажата — предмет, а не картинка."""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._checked = checked
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedSize(40, 24)
        self._pos = Tween(self, 1.0 if checked else 0.0, motion.SWITCH_MS)
        self._press = Tween(self, 0.0, motion.PRESS_MS)
        self._hover = Tween(self, 0.0, motion.HOVER_MS, motion.EASE)

    def isChecked(self) -> bool:  # noqa: N802 (в тон Qt)
        return self._checked

    def set_checked(self, on: bool) -> None:
        """Выставить из настроек, не порождая toggled."""
        if on != self._checked:
            self._checked = on
            self._pos.to(1.0 if on else 0.0, not motion.reduced_motion())

    def _flip(self) -> None:
        self._checked = not self._checked
        self._pos.to(1.0 if self._checked else 0.0, not motion.reduced_motion())
        self.toggled.emit(self._checked)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._press.to(1.0, not motion.reduced_motion())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or not self.isEnabled():
            return
        self._press.to(0.0, not motion.reduced_motion())
        if self.rect().contains(event.position().toPoint()):
            self._flip()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._flip()
        else:
            super().keyPressEvent(event)

    def enterEvent(self, event) -> None:
        self._hover.to(1.0)

    def leaveEvent(self, event) -> None:
        self._hover.to(0.0)
        self._press.to(0.0)

    def focusInEvent(self, event) -> None:
        self.update()

    def focusOutEvent(self, event) -> None:
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        t = self._pos.value
        track = theme.mix(theme.TRACK, theme.ACCENT, t)
        if self._hover.value and self.isEnabled():
            track = theme.mix(track.name(), "#FFFFFF", 0.08 * self._hover.value)
        if not self.isEnabled():
            track.setAlphaF(0.45)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        radius = rect.height() / 2
        painter.drawRoundedRect(rect, radius, radius)
        if self.hasFocus():
            painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(rect.adjusted(-0.5, -0.5, 0.5, 0.5), radius, radius)
            painter.setPen(Qt.NoPen)

        size = rect.height() - 4
        stretch = 5 * self._press.value
        left = rect.left() + 2
        right = rect.right() - 2 - size - stretch
        x = left + (right - left) * t
        painter.setBrush(QColor(0, 0, 0, 50))
        painter.drawRoundedRect(QRectF(x, rect.top() + 2.6, size + stretch, size),
                                size / 2, size / 2)
        painter.setBrush(QColor("#FFFFFF") if self.isEnabled() else QColor("#9A9AA4"))
        painter.drawRoundedRect(QRectF(x, rect.top() + 2, size + stretch, size),
                                size / 2, size / 2)


# --- сегменты ----------------------------------------------------------------------- #

class Segmented(QWidget):
    """Сегментный переключатель (NSSegmentedControl): подложка выбранного
    сегмента переезжает к новому, а не перескакивает."""

    changed = Signal(str)

    def __init__(self, items: list[tuple[str, str]], current: str = "", parent=None) -> None:
        super().__init__(parent)
        self.items = items
        self._index = self._find(current)
        self._pos = Tween(self, float(self._index), motion.SEGMENT_MS, motion.EASE_OUT)
        self._hover = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._font = ui_font(12.5, QFont.Medium)
        metrics = QFontMetricsF(self._font)
        segment = max(metrics.horizontalAdvance(text) for _, text in items) + 24
        self.setFixedWidth(int(segment * len(items) + 4))

    def _find(self, key: str) -> int:
        return max(0, next((i for i, (k, _) in enumerate(self.items) if k == key), 0))

    def value(self) -> str:
        return self.items[self._index][0]

    def set_value(self, key: str) -> None:
        index = self._find(key)
        if index != self._index:
            self._index = index
            self._pos.to(float(index), not motion.reduced_motion())

    def _choose(self, index: int) -> None:
        index = max(0, min(len(self.items) - 1, index))
        if index != self._index:
            self._index = index
            self._pos.to(float(index), not motion.reduced_motion())
            self.changed.emit(self.items[index][0])

    def _segment_at(self, x: float) -> int:
        width = (self.width() - 4) / len(self.items)
        return max(0, min(len(self.items) - 1, int((x - 2) // width)))

    def mouseMoveEvent(self, event) -> None:
        index = self._segment_at(event.position().x())
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = -1
        self.update()

    def mousePressEvent(self, event) -> None:
        # Выбор — на нажатие, а не на отпускание: переключатель отвечает сразу.
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._choose(self._segment_at(event.position().x()))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Left:
            self._choose(self._index - 1)
        elif event.key() == Qt.Key_Right:
            self._choose(self._index + 1)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(255, 255, 255, 30 if self.hasFocus() else 14), 1))
        painter.setBrush(QColor(theme.SEGMENT_BG))
        painter.drawRoundedRect(rect, 8, 8)
        width = (self.width() - 4) / len(self.items)
        thumb = QRectF(2 + self._pos.value * width, 2, width, self.height() - 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.drawRoundedRect(thumb.translated(0, 1), 6.5, 6.5)
        painter.setBrush(QColor(theme.SEGMENT_THUMB))
        painter.drawRoundedRect(thumb, 6.5, 6.5)
        # Светлая грань сверху — тот же приём, что у панели записи.
        painter.setPen(QPen(QColor(255, 255, 255, 22), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(thumb.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)

        painter.setFont(self._font)
        for index, (_, text) in enumerate(self.items):
            segment = QRectF(2 + index * width, 0, width, self.height())
            near = max(0.0, 1.0 - abs(self._pos.value - index))
            emphasis = max(near, 0.55 if index == self._hover else 0.0)
            colour = theme.mix(theme.TEXT_DIM, theme.TEXT, emphasis)
            if not self.isEnabled():
                colour.setAlphaF(0.5)
            painter.setPen(colour)
            painter.drawText(segment, Qt.AlignCenter, text)


# --- поле горячей клавиши ------------------------------------------------------------ #

_MODIFIER_KEYS = {Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, Qt.Key_AltGr}
_DISPLAY = {"Meta": "Win", "Ctrl": "Ctrl", "Alt": "Alt", "Shift": "Shift"}


def _parts(sequence: str) -> list[str]:
    if not sequence:
        return []
    return [_DISPLAY.get(p, p) for p in sequence.split("+")]


class HotkeyEdit(QWidget):
    """Поле сочетания клавиш: клик — «Нажмите сочетание…», Esc — отмена,
    Backspace — очистить. Значение рисуется клавишами-«кнопками».

    `capturing(bool)` нужен программе, чтобы на время записи сочетания снять
    глобальные хоткеи: иначе Ctrl+Alt+R не дошёл бы до поля, а запустил запись.
    """

    changed = Signal(str)
    capturing = Signal(bool)

    def __init__(self, value: str = "", parent=None) -> None:
        super().__init__(parent)
        self._value = value
        self._capturing = False
        self._held = ""              # модификаторы, зажатые прямо сейчас
        self._error = False
        self.setFocusPolicy(Qt.ClickFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(200, 30)
        self._font = ui_font(12, QFont.Medium)
        self._active = Tween(self, 0.0, motion.HOVER_MS, motion.EASE)
        self._hover = Tween(self, 0.0, motion.HOVER_MS, motion.EASE)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        self.update()

    def set_error(self, error: bool) -> None:
        """Сочетание занято — рамка акцентная, пока его не сменят."""
        self._error = error
        self.update()

    # ---- захват -----------------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.setFocus(Qt.MouseFocusReason)
            self._start()

    def _start(self) -> None:
        if self._capturing:
            return
        self._capturing = True
        self._held = ""
        self._active.to(1.0)
        self.capturing.emit(True)

    def _stop(self) -> None:
        if not self._capturing:
            return
        self._capturing = False
        self._held = ""
        self._active.to(0.0)
        self.capturing.emit(False)

    def focusOutEvent(self, event) -> None:
        self._stop()

    def keyPressEvent(self, event) -> None:
        if not self._capturing:
            if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
                self._start()
            return
        key = event.key()
        mods = event.modifiers() & (Qt.ControlModifier | Qt.AltModifier
                                    | Qt.ShiftModifier | Qt.MetaModifier)
        if key in _MODIFIER_KEYS:
            self._held = QKeySequence(mods.value).toString(QKeySequence.PortableText).rstrip("+")
            self.update()
            return
        if key == Qt.Key_Escape and not mods:
            self._stop()
            self.clearFocus()
            return
        if key in (Qt.Key_Backspace, Qt.Key_Delete) and not mods:
            self._commit("")
            return
        is_function = Qt.Key_F1 <= key <= Qt.Key_F24
        # Без Ctrl/Alt/Win глобальное сочетание перехватывало бы обычный набор.
        if not (mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) \
                and not is_function:
            return
        text = QKeySequence(mods.value | key).toString(QKeySequence.PortableText)
        if parse_hotkey(text) is None:
            return
        self._commit(text)

    def keyReleaseEvent(self, event) -> None:
        if self._capturing and event.key() in _MODIFIER_KEYS:
            mods = event.modifiers() & (Qt.ControlModifier | Qt.AltModifier
                                        | Qt.ShiftModifier | Qt.MetaModifier)
            self._held = QKeySequence(mods.value).toString(QKeySequence.PortableText).rstrip("+")
            self.update()

    def _commit(self, text: str) -> None:
        self._value = text
        self._error = False
        self._stop()
        self.clearFocus()
        self.changed.emit(text)

    def enterEvent(self, event) -> None:
        self._hover.to(1.0)

    def leaveEvent(self, event) -> None:
        self._hover.to(0.0)

    # ---- отрисовка ----------------------------------------------------------------
    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        idle_border = theme.mix("#2E2E35", "#44444C", self._hover.value)
        border = theme.mix(idle_border.name(), theme.ACCENT, self._active.value)
        if self._error and not self._capturing:
            border = QColor(theme.ACCENT)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QColor(theme.SEGMENT_BG))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setFont(self._font)

        if self._capturing:
            parts = _parts(self._held)
            x = self._draw_caps(painter, parts, 8)
            painter.setPen(QColor(theme.TEXT_DIM))
            painter.drawText(QRectF(x + (6 if parts else 0), 0, self.width() - x - 8,
                                    self.height()),
                             Qt.AlignVCenter | Qt.AlignLeft,
                             "…" if parts else tr("keys.press"))
            return
        parts = _parts(self._value)
        if not parts:
            painter.setPen(QColor(theme.TEXT_DIM))
            painter.drawText(rect.adjusted(10, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft,
                             tr("keys.none"))
            return
        self._draw_caps(painter, parts, 8)

    def _draw_caps(self, painter: QPainter, parts: list[str], x: float) -> float:
        metrics = QFontMetricsF(self._font)
        for index, part in enumerate(parts):
            width = max(metrics.horizontalAdvance(part) + 14, 22)
            cap = QRectF(x, 5, width, self.height() - 10)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.SEGMENT_THUMB))
            painter.drawRoundedRect(cap, 5, 5)
            painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(cap.adjusted(0.5, 0.5, -0.5, -0.5), 4.5, 4.5)
            painter.setPen(QColor(theme.TEXT))
            painter.drawText(cap, Qt.AlignCenter, part)
            x += width
            if index < len(parts) - 1:
                painter.setPen(QColor(theme.TEXT_DIM))
                painter.drawText(QRectF(x, 0, 12, self.height()), Qt.AlignCenter, "+")
                x += 12
        return x


# --- полоса прогресса --------------------------------------------------------------- #

class ProgressLine(QWidget):
    """Тонкая полоса прогресса: значение догоняет новое линейно (как любой
    прогресс — ускорение здесь выдавало бы неправду о скорости)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._value = Tween(self, 0.0, 250, QEasingCurve(QEasingCurve.Linear))

    def set_fraction(self, fraction: float) -> None:
        self._value.to(fraction, animate=fraction >= self._value.value)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.TRACK))
        painter.drawRoundedRect(rect, radius, radius)
        if self._value.value > 0:
            painter.setBrush(QColor(theme.ACCENT))
            width = max(rect.height(), rect.width() * min(1.0, self._value.value))
            painter.drawRoundedRect(QRectF(rect.left(), rect.top(), width, rect.height()),
                                    radius, radius)


# --- ползунок со значением ------------------------------------------------------------- #

class Slider(QSlider):
    """Ползунок, нарисованный целиком самим: дорожка 4 px, заполнение акцентом,
    белый бегунок. Под курсором бегунок чуть светлеет, под пальцем — чуть
    растёт: видно, что его взяли. Табличные правила QSS для QSlider в
    Windows-стиле растягивали дорожку на всю высоту виджета."""

    THUMB = 16.0

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.setFixedHeight(24)
        self.setCursor(Qt.PointingHandCursor)
        self._hover = Tween(self, 0.0, motion.HOVER_MS, motion.EASE)
        self._press = Tween(self, 0.0, motion.PRESS_MS)
        self.sliderPressed.connect(lambda: self._press.to(1.0, not motion.reduced_motion()))
        self.sliderReleased.connect(lambda: self._press.to(0.0, not motion.reduced_motion()))

    def enterEvent(self, event) -> None:
        self._hover.to(1.0)

    def leaveEvent(self, event) -> None:
        self._hover.to(0.0)

    def _thumb_x(self) -> float:
        span = self.maximum() - self.minimum() or 1
        usable = self.width() - self.THUMB
        return self.THUMB / 2 + usable * (self.value() - self.minimum()) / span

    def mousePressEvent(self, event) -> None:
        # Клик по дорожке ставит бегунок туда, куда нажали, а не шагает к нему
        # страницами, как принято в Windows, — и сразу позволяет тянуть.
        if event.button() == Qt.LeftButton and self.isEnabled():
            self._jump(event.position().x())
            self.setSliderDown(True)
            self.sliderPressed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.isSliderDown():
            self._jump(event.position().x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.isSliderDown():
            self.setSliderDown(False)
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _jump(self, x: float) -> None:
        usable = max(self.width() - self.THUMB, 1)
        fraction = min(max((x - self.THUMB / 2) / usable, 0.0), 1.0)
        self.setValue(round(self.minimum() + fraction * (self.maximum() - self.minimum())))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        centre_y = self.height() / 2
        track = QRectF(self.THUMB / 2 - 2, centre_y - 2, self.width() - self.THUMB + 4, 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.TRACK))
        painter.drawRoundedRect(track, 2, 2)
        x = self._thumb_x()
        if self.isEnabled():
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(QRectF(track.left(), track.top(), x - track.left(), 4), 2, 2)
        radius = self.THUMB / 2 * (1.0 + 0.12 * self._press.value)
        if self.hasFocus():
            ring = QColor(theme.ACCENT)
            ring.setAlpha(90)
            painter.setBrush(ring)
            painter.drawEllipse(QPointF(x, centre_y), radius + 3, radius + 3)
        painter.setBrush(QColor(0, 0, 0, 60))
        painter.drawEllipse(QPointF(x, centre_y + 1), radius, radius)
        thumb = theme.mix("#FFFFFF", "#FFE3DA", self._hover.value) if self.isEnabled()             else QColor(theme.TEXT_DIM)
        painter.setBrush(thumb)
        painter.drawEllipse(QPointF(x, centre_y), radius, radius)


class ValueSlider(QWidget):
    """Ползунок и значение справа. `changed` — на каждый шаг: всё, что от него
    зависит (подпись «итог: …»), обязано следовать за пальцем 1:1."""

    changed = Signal(int)

    def __init__(self, low: int, high: int, value: int, fmt: Callable[[int], str],
                 step: int = 1, width: int = 180, parent=None) -> None:
        super().__init__(parent)
        self._fmt = fmt
        self.slider = Slider()
        self.slider.setRange(low, high)
        self.slider.setSingleStep(step)
        self.slider.setPageStep(step * 2)
        self.slider.setFixedWidth(width)
        self.slider.setValue(int(value))
        self._step = step
        self.text = label(fmt(int(value)), "value")
        digits = self.text.font()
        digits.setFeature(QFont.Tag("tnum"), 1)      # цифры не пляшут по ширине
        self.text.setFont(digits)
        self.text.setMinimumWidth(44)
        self.text.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(self.slider)
        row.addWidget(self.text)
        self.slider.valueChanged.connect(self._on_value)

    def _on_value(self, value: int) -> None:
        snapped = round(value / self._step) * self._step
        if snapped != value:
            self.slider.setValue(snapped)
            return
        self.text.setText(self._fmt(value))
        self.changed.emit(value)

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, value: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(value))
        self.slider.blockSignals(False)
        self.text.setText(self._fmt(int(value)))


# --- карточки настроек (сгруппированные списки macOS) -------------------------------- #

class Group(QFrame):
    """Скруглённая карточка со строками, разделёнными волосяной линией."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(0)

    def add(self, row: QWidget) -> QWidget:
        self._column.addWidget(row)
        return row

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(255, 255, 255, 14), 1))
        painter.setBrush(QColor(theme.CARD))
        painter.drawRoundedRect(rect, 12, 12)
        rows = [self._column.itemAt(i).widget() for i in range(self._column.count())]
        rows = [w for w in rows if w is not None and w.isVisible()]
        painter.setPen(QPen(QColor(theme.HAIRLINE), 1))
        for widget in rows[:-1]:
            y = widget.geometry().bottom() + 0.5
            painter.drawLine(QPointF(16, y), QPointF(self.width() - 16, y))


class Row(QWidget):
    """Строка настройки: заголовок и пояснение слева, элемент управления справа."""

    def __init__(self, title: str, control: QWidget | None = None, note: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(48)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 9, 14, 9)
        layout.setSpacing(14)
        text = QVBoxLayout()
        text.setSpacing(2)
        # Раскладку цепляем к строке ДО любых setVisible: виджет без родителя,
        # которому сказали «покажись», Qt открывает отдельным окном.
        layout.addLayout(text, 1)
        self.title = label(title, "rowTitle", parent=self)
        text.addWidget(self.title)
        self.note = label(note, "rowNote", wrap=True, parent=self)
        text.addWidget(self.note)
        self.note.setHidden(not note)
        self.control = control
        if control is not None:
            layout.addWidget(control, 0, Qt.AlignVCenter)

    def set_note(self, text: str, warn: bool = False) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))
        if self.note.property("warn") != warn:
            self.note.setProperty("warn", warn)
            self.note.style().unpolish(self.note)
            self.note.style().polish(self.note)


def section(title: str) -> QLabel:
    widget = label(title.upper(), "sectionTitle")
    widget.setContentsMargins(16, 18, 0, 6)
    return widget


def hbox(*widgets: QWidget, spacing: int = 8) -> QWidget:
    """Несколько элементов в правой части строки."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for widget in widgets:
        row.addWidget(widget)
    return box


__all__ = [
    "Group", "HotkeyEdit", "ProgressLine", "Row", "Segmented", "Slider", "Switch", "Tween",
    "ValueSlider", "crossfade", "hbox", "label", "section", "ui_font",
]
