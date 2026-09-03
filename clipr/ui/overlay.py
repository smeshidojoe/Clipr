"""The floating control panel.

Always on top, draggable anywhere, and excluded from the capture itself via
WDA_EXCLUDEFROMCAPTURE, so it can stay visible while recording. That exclusion
is why the window is opaque and rounded by a window region instead of by
``WA_TranslucentBackground``: Windows refuses the affinity on layered windows.

Motion notes: the panel tracks the pointer 1:1 (respecting the grab offset),
resists past the screen edge instead of stopping dead, and on release projects
the flick's momentum forward before springing to the nearest edge — carrying the
release velocity into the spring so there is no seam between drag and animation.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import (
    QAbstractAnimation,
    QPoint,
    QRect,
    QSize,
    QTimer,
    QVariantAnimation,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QMouseEvent, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLayout, QVBoxLayout, QWidget

from ..winapi import exclude_from_capture, round_window_corners
from . import motion, theme
from .icons import glyph
from .widgets import PressButton

EDGE_MARGIN = 18
CORNER_RADIUS = 16

# Порог броска: ниже — панель остаётся там, где её отпустили. Быстрое движение
# мышью иначе улетало через пол-экрана, а медленное — уезжало вбок от курсора.
FLICK_SPEED = 320.0          # px/с
MAX_THROW = 200.0            # px, потолок дальности броска
DECELERATION = 0.99          # 0.998 = «как скролл», для маленькой панели слишком далеко
SETTLE_DAMPING, SETTLE_RESPONSE = 0.62, 0.30   # перелёт и возврат к точке отпускания
FLICK_DAMPING, FLICK_RESPONSE = 0.78, 0.34


def _alive(obj) -> bool:
    """True when the Qt object behind a Python wrapper still exists.

    Animations are started with ``DeleteWhenStopped``; keeping a Python
    reference to one that Qt already freed is what produced
    "Internal C++ object already deleted" on the next interaction.
    """
    if obj is None:
        return False
    try:
        from shiboken6 import isValid
    except ImportError:
        return True
    return bool(isValid(obj))


def _icon_button(name: str, tooltip: str) -> PressButton:
    """Кнопка-пиктограмма. Панель не берёт фокус, поэтому и кнопки его не берут."""
    button = PressButton(radius=9)
    button.setObjectName("icon")
    button.setIcon(glyph(name))
    button.setIconSize(QSize(18, 18))
    button.setToolTip(tooltip)
    button.setFocusPolicy(Qt.NoFocus)
    return button


class RecordDot(QWidget):
    """Breathing red dot — the panel's ongoing-status feedback."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(12, 12)
        self._active = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)

    def set_active(self, active: bool, blinking: bool = True) -> None:
        self._active = active
        if active and blinking and not motion.reduced_motion():
            self._timer.start()
        else:
            self._timer.stop()
            self._phase = 0.0
        self.update()

    def _tick(self) -> None:
        self._phase += 0.09
        self.update()

    def paintEvent(self, _event) -> None:
        if not self._active:
            return
        pulse = 0.65 + 0.35 * (0.5 + 0.5 * math.cos(self._phase))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        colour = QColor(theme.DANGER)
        colour.setAlphaF(pulse)
        painter.setBrush(colour)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(self.rect().center(), 4, 4)


class OverlayPanel(QWidget):
    """Compact always-on-top controller."""

    select_region_requested = Signal()
    start_requested = Signal()
    pause_requested = Signal()
    stop_requested = Signal()
    open_folder_requested = Signal()
    settings_requested = Signal()
    moved = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._drag_offset: QPoint | None = None
        self._tracker = motion.VelocityTracker()
        self._anim_x: QVariantAnimation | None = None
        self._anim_y: QVariantAnimation | None = None
        self._anim_w: QVariantAnimation | None = None
        self._fade: QVariantAnimation | None = None

        self._build()
        self.set_state("idle")
        self.adjustSize()

    # ---- construction ----------------------------------------------------
    def _build(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(13, 10, 13, 10)
        row.setSpacing(8)
        # Ширину анимируем сами — раскладка не должна навязывать окну размер.
        row.setSizeConstraint(QLayout.SetNoConstraint)

        self.dot = RecordDot()
        row.addWidget(self.dot)

        stack = QVBoxLayout()
        stack.setSpacing(0)
        self.timecode = QLabel("00:00.0")
        self.timecode.setObjectName("timecode")
        # Цифры одной ширины: иначе таймкод «дышит» на каждой десятой секунды и
        # тянет за собой всю панель.
        digits = self.timecode.font()
        digits.setFeature(QFont.Tag("tnum"), 1)
        self.timecode.setFont(digits)
        self.region_hint = QLabel("Весь экран")
        self.region_hint.setObjectName("hint")
        stack.addWidget(self.timecode)
        stack.addWidget(self.region_hint)
        row.addLayout(stack)
        row.addSpacing(6)

        self.btn_region = _icon_button("region", "Выбрать область записи")

        self.btn_record = PressButton("Запись")
        self.btn_record.setObjectName("primary")
        self.btn_record.setToolTip("Начать запись")
        self.btn_record.setIcon(glyph("record", colour="#FFFFFF"))
        self.btn_record.setIconSize(QSize(14, 14))
        self.btn_record.setFocusPolicy(Qt.NoFocus)

        self.btn_pause = _icon_button("pause", "Пауза")
        self.btn_stop = _icon_button("stop", "Стоп и редактирование")
        self.btn_folder = _icon_button("folder", "Открыть папку с записями")
        self.btn_settings = _icon_button("settings", "Настройки")
        self.btn_close = _icon_button("close", "Скрыть панель (вернуть — клик по иконке в трее)")

        for widget in (
            self.btn_region, self.btn_record, self.btn_pause,
            self.btn_stop, self.btn_folder, self.btn_settings, self.btn_close,
        ):
            row.addWidget(widget)

        self.btn_region.clicked.connect(self.select_region_requested)
        self.btn_record.clicked.connect(self.start_requested)
        self.btn_pause.clicked.connect(self.pause_requested)
        self.btn_stop.clicked.connect(self.stop_requested)
        self.btn_folder.clicked.connect(self.open_folder_requested)
        self.btn_settings.clicked.connect(self.settings_requested)
        self.btn_close.clicked.connect(self.hide_animated)

        self.adjustSize()

    # ---- public API ------------------------------------------------------
    def set_region_label(self, text: str) -> None:
        self.region_hint.setText(text)
        self._morph_width()      # подпись меняет ширину так же, как смена состояния

    def set_timecode(self, seconds: float) -> None:
        minutes, rest = divmod(max(seconds, 0.0), 60)
        self.timecode.setText(f"{int(minutes):02d}:{rest:04.1f}")

    def set_state(self, state: str) -> None:
        """state: idle | recording | paused | busy."""
        recording = state == "recording"
        paused = state == "paused"
        active = recording or paused
        busy = state == "busy"

        self.btn_record.setVisible(not active)
        self.btn_pause.setVisible(active)
        self.btn_stop.setVisible(active)
        self.btn_region.setEnabled(not active and not busy)
        self.btn_record.setEnabled(not busy)
        self.btn_pause.setIcon(glyph("play" if paused else "pause"))
        self.btn_pause.setToolTip("Продолжить" if paused else "Пауза")
        self.dot.set_active(active, blinking=recording)
        if not active:
            self.set_timecode(0.0)
        self._morph_width()

    def _morph_width(self) -> None:
        """Панель меняет ширину под новый набор кнопок — плавно.

        Смена состояния переставляет кнопки, и ширина скачет (382 → 403 → 393
        px): панель рывком уезжала вправо. Растём от центра, чтобы движение
        читалось как «панель стала шире», а не «панель прыгнула».
        """
        layout = self.layout()
        layout.activate()
        target = self.sizeHint().width()
        if target == self.width():
            return
        self._stop_anim("_anim_w")
        if not self.isVisible() or motion.reduced_motion():
            self._apply_width(target)
            return
        damping, response = motion.PANEL_MORPH
        self._anim_w = motion.spring_animate(
            self, float(self.width()), float(target), self._apply_width,
            damping=damping, response=response,
            on_finished=lambda: setattr(self, "_anim_w", None),
        )

    def _apply_width(self, value: float) -> None:
        width = max(round(value), 1)
        centre = self.x() + self.width() / 2.0
        self.setGeometry(round(centre - width / 2.0), self.y(), width, self.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        round_window_corners(int(self.winId()), self.width(), self.height(), CORNER_RADIUS)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Ставим на каждом показе: фейд делает окно слоистым, и полагаться на
        # то, что аффинити переживёт смену стиля, не стоит.
        exclude_from_capture(int(self.winId()), True)

    # ---- show / hide -----------------------------------------------------
    def toggle(self, instant: bool = False) -> None:
        if self.isVisible():
            self.hide_animated(instant)
        else:
            self.show_animated(instant)

    def show_animated(self, instant: bool = False) -> None:
        """Появление панели.

        Сдвига здесь намеренно нет, а фейд короткий: панель дёргают горячей
        клавишей десятки раз в день, а действия с клавиатуры анимировать нельзя
        — анимация превращает мгновенную команду в ожидание. По хоткею
        (``instant``) панель появляется вообще без перехода.
        """
        self._stop_anim("_fade")
        if instant or motion.reduced_motion():
            self.setWindowOpacity(1.0)
            self.show()
            self.raise_()
            return
        if not self.isVisible():
            self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._fade = self._animate_opacity(1.0, duration=motion.PANEL_IN_MS)

    def hide_animated(self, instant: bool = False) -> None:
        if not self.isVisible():
            return
        self._stop_anim("_fade")
        if instant or motion.reduced_motion():
            self.hide()
            return
        # Выход быстрее входа: на исчезающее уже не смотрят.
        self._fade = self._animate_opacity(
            0.0, duration=motion.PANEL_OUT_MS, on_finished=self.hide)

    def _animate_opacity(self, target: float, duration: int,
                         on_finished=None) -> QVariantAnimation:
        anim = QVariantAnimation(self)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(target)
        anim.setDuration(duration)
        anim.setEasingCurve(motion.ENTER_CURVE)
        anim.valueChanged.connect(self.setWindowOpacity)
        if on_finished:
            anim.finished.connect(on_finished)
        anim.finished.connect(lambda: setattr(self, "_fade", None))
        anim.start(QAbstractAnimation.DeleteWhenStopped)
        return anim

    def _stop_anim(self, attribute: str) -> None:
        anim = getattr(self, attribute, None)
        if _alive(anim):
            anim.stop()
        setattr(self, attribute, None)

    # ---- dragging --------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.LeftButton:
            return
        # Grabbing a moving panel takes it over mid-flight.
        self._stop_anim("_anim_x")
        self._stop_anim("_anim_y")
        self._stop_anim("_anim_w")
        self._drag_offset = event.globalPosition().toPoint() - self.pos()
        self._tracker.clear()
        self._tracker.add(time.monotonic(), *event.globalPosition().toTuple())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is None:
            return
        global_pos = event.globalPosition().toPoint()
        self._tracker.add(time.monotonic(), *event.globalPosition().toTuple())
        self.move(self._resist(global_pos - self._drag_offset))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is None:
            return
        self._drag_offset = None
        release = self._clamp(self.pos())
        if motion.reduced_motion():
            # Без инерции: панель остаётся там, где её отпустили, и лишь
            # возвращается в границы экрана.
            self.move(release)
            self.moved.emit(self.pos())
            return

        vx, vy = self._tracker.velocity()
        speed = math.hypot(vx, vy)
        # Плавный переход между «просто переставил» и «бросил»: ниже порога
        # броска нет вовсе, дальше он набирает силу — иначе на самом пороге
        # панель скачком улетала бы на пару десятков пикселей.
        ramp = min(max((speed - FLICK_SPEED) / FLICK_SPEED, 0.0), 1.0)
        damping = SETTLE_DAMPING + (FLICK_DAMPING - SETTLE_DAMPING) * ramp
        response = SETTLE_RESPONSE + (FLICK_RESPONSE - SETTLE_RESPONSE) * ramp

        throw, slowdown = self._throw(vx, vy, ramp)
        # Подрезали дальность — подрезаем и скорость: иначе пружина, которой
        # отдали исходные px/с, перелетит укороченную цель на десятки пикселей.
        vx, vy = vx * slowdown, vy * slowdown
        wanted = release + throw
        target = self._clamp(wanted)
        # Бросок, упёршийся в край экрана, гасим по этой оси: перелетать за
        # границу некуда, и пружина без запаса выглядит как честный упор.
        damping_x = 1.0 if target.x() != wanted.x() else damping
        damping_y = 1.0 if target.y() != wanted.y() else damping
        self._anim_x = self._spring_axis(self.x(), target.x(), damping_x, response, vx, "x")
        self._anim_y = self._spring_axis(self.y(), target.y(), damping_y, response, vy, "y")

    def _throw(self, vx: float, vy: float, ramp: float) -> tuple[QPoint, float]:
        """Куда бросок докатился бы, и во сколько раз его пришлось укоротить.

        При ramp == 0 бросок нулевой: цель совпадает с точкой отпускания, и
        остаточная скорость даёт только короткий перелёт с возвратом в неё.
        """
        if ramp <= 0.0:
            return QPoint(0, 0), 1.0
        dx = motion.project(vx, DECELERATION) * ramp
        dy = motion.project(vy, DECELERATION) * ramp
        distance = math.hypot(dx, dy)
        scale = 1.0
        if distance > MAX_THROW:
            scale = MAX_THROW / distance
            dx, dy = dx * scale, dy * scale
        return QPoint(round(dx), round(dy)), scale

    def _bounds(self) -> QRect:
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        return QRect(
            area.left() + EDGE_MARGIN,
            area.top() + EDGE_MARGIN,
            max(area.width() - self.width() - EDGE_MARGIN * 2, 0),
            max(area.height() - self.height() - EDGE_MARGIN * 2, 0),
        )

    def _clamp(self, point: QPoint) -> QPoint:
        bounds = self._bounds()
        return QPoint(
            min(max(point.x(), bounds.left()), bounds.left() + bounds.width()),
            min(max(point.y(), bounds.top()), bounds.top() + bounds.height()),
        )

    def _resist(self, point: QPoint) -> QPoint:
        """Soft boundary: past the edge the panel follows less and less."""
        bounds = self._bounds()
        clamped = self._clamp(point)
        over_x = point.x() - clamped.x()
        over_y = point.y() - clamped.y()
        return QPoint(
            clamped.x() + round(motion.rubberband(over_x, max(bounds.width(), 1))),
            clamped.y() + round(motion.rubberband(over_y, max(bounds.height(), 1))),
        )

    def _spring_to(
        self, target: QPoint, damping: float, response: float, vx: float = 0.0, vy: float = 0.0
    ) -> None:
        """Two independent springs — one 2D spring desyncs when vx != vy."""
        self._anim_x = self._spring_axis(self.x(), target.x(), damping, response, vx, "x")
        self._anim_y = self._spring_axis(self.y(), target.y(), damping, response, vy, "y")

    def _spring_axis(
        self, start: int, end: int, damping: float, response: float, velocity: float, axis: str
    ):
        # Случай start == end с ненулевой скоростью намеренно не отсекаем:
        # это и есть перелёт с возвратом в ту же точку.
        if axis == "x":
            def apply(value: float) -> None:
                self.move(round(value), self.y())
        else:
            def apply(value: float) -> None:
                self.move(self.x(), round(value))

        def done() -> None:
            setattr(self, f"_anim_{axis}", None)
            self.moved.emit(self.pos())

        return motion.spring_animate(
            self, float(start), float(end), apply,
            damping=damping, response=response, velocity=velocity,
            on_finished=done,
        )
