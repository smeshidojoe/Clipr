"""Пружинное движение для виджетов Qt.

Пружина описывается двумя понятными числами вместо физической тройки
(масса/жёсткость/затухание): *затухание* (перелёт; 1.0 — критическое, без
отскока) и *отклик* (за сколько секунд значение доходит до цели). Решение
считается аналитически и подаётся в Qt через обычную линейную анимацию —
кастомная ``QEasingCurve`` здесь не годится: Qt копирует кривые внутри себя и
теряет питоновский колбэк (падало с access violation).

По умолчанию затухание 1.0; отскок оставляем движению, которое запустил сам
жест пользователя.
"""

from __future__ import annotations

import math
import time

from PySide6.QtCore import (
    QAbstractAnimation, QEasingCurve, QObject, QPointF, QVariantAnimation,
)

# --- токены движения ------------------------------------------------------- #
# Все длительности и пружины живут здесь. Пять почти одинаковых значений,
# набитых по файлам вручную, — верный способ получить интерфейс, который
# движется вразнобой.

# Пружины: (затухание, отклик).
PANEL_MORPH = (0.9, 0.28)    # панель меняет ширину при смене состояния
PANEL_SETTLE = (0.62, 0.30)  # отпускание без броска: перелёт и возврат
PANEL_FLICK = (0.78, 0.34)   # отпускание с броском

# Длительности, мс. Потолок для интерфейса — 300 мс; выход всегда быстрее входа.
PRESS_MS = 140               # отклик на нажатие
PANEL_IN_MS = 120            # показ панели: часто повторяется, значит коротко
PANEL_OUT_MS = 90            # скрытие ещё короче — выход не рассматривают
HOVER_MS = 120               # подсветка под курсором
ENTER_MS = 180               # появление окна, рамки, затемнения
EXIT_MS = 110                # исчезновение
FLASH_MS = 220               # разовая вспышка «готово»

SWITCH_MS = 180              # бегунок переключателя
SEGMENT_MS = 200             # подложка сегментов и меню разделов
PAGE_MS = 160                # смена страницы настроек


def _bezier(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    """cubic-bezier как в CSS. Это встроенная кривая Qt (BezierSpline), а не
    питоновский колбэк, — Qt спокойно её копирует (так же сделано в Scribe)."""
    curve = QEasingCurve(QEasingCurve.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1, 1))
    return curve


# Кривые. Сильный ease-out — для всего, что появляется или отвечает на
# нажатие: движение начинается сразу, и интерфейс кажется отзывчивым.
# ease-in-out — для того, что едет по экрану из точки в точку. Мягкий `ease` —
# только для смены цвета под курсором.
EASE_OUT = _bezier(0.23, 1, 0.32, 1)
EASE_IN_OUT = _bezier(0.77, 0, 0.175, 1)
EASE = _bezier(0.25, 0.1, 0.25, 1)
ENTER_CURVE = EASE_OUT


_reduced_motion = False


def reduced_motion() -> bool:
    """True when the user asked for less movement.

    Reduced motion is not *no* feedback — springs are swapped for a short
    non-vestibular fade/ease, never dropped outright.
    """
    return _reduced_motion


def set_reduced_motion(enabled: bool) -> None:
    global _reduced_motion
    _reduced_motion = bool(enabled)


def spring_position(
    t: float, start: float, end: float,
    damping: float, response: float, velocity: float = 0.0,
) -> float:
    """Положение пружины в момент ``t`` в тех же единицах, что start/end.

    Считаем в абсолютных величинах, а не в нормированном прогрессе 0..1: только
    так работает случай ``start == end`` при ненулевой скорости — панель уходит
    за точку отпускания и возвращается к ней. При нормировке этот случай
    вырождается (делить пришлось бы на нулевой пролёт).

    ``velocity`` — производная в момент 0 (px/с), то есть скорость пальца в миг
    отпускания: анимация продолжает движение ровно с неё, без шва.
    """
    if t <= 0.0:
        return start
    omega = 2.0 * math.pi / max(response, 1e-4)
    zeta = max(damping, 0.0)
    offset = start - end
    if zeta < 1.0:
        omega_d = omega * math.sqrt(1.0 - zeta * zeta)
        decay = math.exp(-zeta * omega * t)
        return end + decay * (
            offset * math.cos(omega_d * t)
            + ((velocity + zeta * omega * offset) / omega_d) * math.sin(omega_d * t)
        )
    decay = math.exp(-omega * t)
    return end + decay * (offset + (velocity + omega * offset) * t)


def spring_value(t: float, damping: float, response: float, velocity: float = 0.0) -> float:
    """Нормированное положение пружины 0 -> 1 (velocity — тоже нормированная)."""
    return spring_position(t, 0.0, 1.0, damping, response, velocity)


def settle_time(damping: float, response: float, epsilon: float = 0.02) -> float:
    """Через сколько пружина визуально успокоится — фиксированной длительности
    у неё нет.

    epsilon намеренно грубый: с 0.005 последняя сотня миллисекунд уходила на
    досаживание в доли пикселя, которого не видно. Это «перцептивная
    длительность» — анимация считается законченной тогда, когда её перестают
    замечать.
    """
    omega = 2.0 * math.pi / max(response, 1e-4)
    zeta = max(damping, 1e-3)
    # Envelope e^(-zeta*omega*t) < epsilon
    return min(math.log(1.0 / epsilon) / (zeta * omega), 3.0)


def spring_animate(
    parent: QObject,
    start: float,
    end: float,
    on_value,
    damping: float = 1.0,
    response: float = 0.4,
    velocity: float = 0.0,
    on_finished=None,
) -> QVariantAnimation | None:
    """Гонит одно скалярное значение от ``start`` к ``end`` по пружине.

    Анимация идёт по линейному прогрессу, а пружина считается в колбэке, а не
    через кастомную ``QEasingCurve``: Qt копирует кривые внутри себя и теряет
    питоновский колбэк за кастомной кривой (падало с access violation).

    ``start`` должен быть текущим экранным значением — тогда прерванная анимация
    продолжается оттуда, где элемент реально виден.
    """
    if start == end and abs(velocity) < 1.0:
        return None                       # двигать нечего
    duration = settle_time(damping, response)

    anim = QVariantAnimation(parent)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Linear)
    if reduced_motion():
        span = end - start
        anim.setDuration(150)
        anim.valueChanged.connect(lambda p: on_value(start + span * _ease_out(p)))
    else:
        anim.setDuration(int(duration * 1000))
        anim.valueChanged.connect(
            lambda p: on_value(
                spring_position(p * duration, start, end, damping, response, velocity)
            )
        )
    anim.finished.connect(lambda: on_value(end))
    if on_finished is not None:
        anim.finished.connect(on_finished)
    anim.start(QAbstractAnimation.DeleteWhenStopped)
    return anim


def _ease_out(progress: float) -> float:
    """Cubic ease-out — the reduced-motion stand-in for a spring."""
    return 1.0 - (1.0 - progress) ** 3


def project(velocity: float, deceleration: float = 0.998) -> float:
    """Where a flick would come to rest — the exponential-decay form Apple ships.

    ``velocity`` is in px/s; the textbook v^2/(2a) is deliberately not used.
    """
    return (velocity / 1000.0) * deceleration / (1.0 - deceleration)


def rubberband(overshoot: float, dimension: float, constant: float = 0.55) -> float:
    """Progressive resistance past a boundary, instead of a hard stop."""
    if dimension <= 0:
        return 0.0
    return (overshoot * dimension * constant) / (dimension + constant * abs(overshoot))


class VelocityTracker:
    """Короткая история позиций, чтобы отдать скорость отпускания пружине."""

    def __init__(self, window: int = 5, max_age: float = 0.09) -> None:
        self._samples: list[tuple[float, float, float]] = []  # t, x, y
        self._window = window
        self._max_age = max_age

    def clear(self) -> None:
        self._samples.clear()

    def add(self, timestamp: float, x: float, y: float) -> None:
        self._samples.append((timestamp, x, y))
        if len(self._samples) > self._window:
            self._samples.pop(0)

    def velocity(self, now: float | None = None) -> tuple[float, float]:
        """px/с по каждой оси, разложенные по отдельности — одна 2D-пружина
        рассинхронизируется, когда скорости по осям разные.

        Учитываются только выборки не старше ``max_age`` ОТ МОМЕНТА ЗАПРОСА.
        Иначе получалось так: подвинул мышь, задержал её на месте, отпустил — и
        панель уезжала, потому что в истории лежала давняя скорость. Новых
        событий движения при неподвижной мыши не приходит, обнулить историю
        некому — значит, отбрасываем её по возрасту.
        """
        if now is None:
            now = time.monotonic()
        recent = [s for s in self._samples if now - s[0] <= self._max_age]
        if len(recent) < 2:
            return 0.0, 0.0
        t0, x0, y0 = recent[0]
        t1, x1, y1 = recent[-1]
        dt = t1 - t0
        if dt <= 1e-4:
            return 0.0, 0.0
        return (x1 - x0) / dt, (y1 - y0) / dt


__all__ = [
    "EASE", "EASE_IN_OUT", "EASE_OUT",
    "ENTER_CURVE", "ENTER_MS", "EXIT_MS", "FLASH_MS", "HOVER_MS",
    "PAGE_MS", "PANEL_IN_MS", "PANEL_OUT_MS", "PRESS_MS", "SEGMENT_MS", "SWITCH_MS",
    "PANEL_FLICK", "PANEL_MORPH", "PANEL_SETTLE",
    "VelocityTracker",
    "project", "reduced_motion", "rubberband", "set_reduced_motion",
    "settle_time", "spring_animate", "spring_position", "spring_value",
]
