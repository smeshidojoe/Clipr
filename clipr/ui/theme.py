"""Shared visual language: colours, type scale, and the app stylesheet.

Type follows the same discipline as the motion: tracking is size-specific
(negative on large text, slightly positive on small labels), and hierarchy comes
from weight + size + leading together rather than size alone.
"""

from __future__ import annotations

ACCENT = "#F5623D"
ACCENT_DIM = "#C44A2D"
# Непрозрачные поверхности. Полупрозрачность потребовала бы
# WA_TranslucentBackground, а такое (layered) окно Windows отказывается прятать
# от захвата экрана — панель попадала бы в собственную запись (см. winapi).
WINDOW = "#141418"
SURFACE = "#1C1C20"
SURFACE_RAISED = "#26262C"
TEXT = "#F2F2F4"
TEXT_DIM = "#9A9AA4"
BORDER = "rgba(255, 255, 255, 0.10)"
BORDER_BRIGHT = "rgba(255, 255, 255, 0.22)"
DANGER = "#E5484D"
OK = "#3DD68C"

# Окно настроек: навигация чуть темнее страницы, карточки — светлее. Иерархия
# поверхностей читается по яркости, а не по рамкам.
SIDEBAR = "#111114"
CARD = "#1E1E23"
CARD_HOVER = "#24242A"
HAIRLINE = "#2A2A30"            # разделители строк внутри карточки
TRACK = "#3A3A42"               # выключенный переключатель, пустая полоса
SEGMENT_BG = "#131317"
SEGMENT_THUMB = "#34343C"


def mix(a: str, b: str, t: float):
    """Линейное смешение цветов: t = 0 -> a, 1 -> b."""
    from PySide6.QtGui import QColor
    ca, cb = QColor(a), QColor(b)
    return QColor.fromRgbF(*(x + (y - x) * t for x, y in zip(ca.getRgbF(), cb.getRgbF())))

QSS = f"""
* {{
    font-family: "Segoe UI Variable Display", "Segoe UI", system-ui, sans-serif;
    color: {TEXT};
}}

QDialog, QWidget#window {{
    background: {WINDOW};
}}

QWidget#panel, QFrame#panel, QWidget#card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    /* Светлая грань сверху — материал ловит свет. Тень окну недоступна:
       она потребовала бы прозрачности, а прозрачное окно нельзя спрятать
       от захвата экрана (см. winapi). */
    border-top: 1px solid rgba(255, 255, 255, 0.17);
    border-radius: 16px;
}}

QMenu {{
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_BRIGHT};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    background: transparent;
    color: {TEXT};
    padding: 7px 26px 7px 14px;
    border-radius: 7px;
    font-size: 13px;
}}
QMenu::item:selected {{ background: {ACCENT}; color: #FFFFFF; }}
QMenu::item:disabled {{ color: {TEXT_DIM}; }}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 5px 8px;
}}

QLabel#timecode {{
    font-size: 19px;
    font-weight: 600;
    letter-spacing: -0.4px;   /* tighten as the type grows */
    color: {TEXT};
}}

QLabel#title {{
    font-size: 17px;
    font-weight: 600;
    letter-spacing: -0.3px;
}}

QLabel#hint, QLabel#caption {{
    font-size: 11px;
    letter-spacing: 0.2px;    /* small text reads better slightly open */
    color: {TEXT_DIM};
}}

QPushButton {{
    background: rgba(255, 255, 255, 0.06);
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 7px 14px;
    font-size: 13px;
    font-weight: 500;
}}
QPushButton:hover  {{ background: rgba(255, 255, 255, 0.12); }}
QPushButton:pressed{{ background: rgba(255, 255, 255, 0.04); }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: rgba(255,255,255,0.05); }}
/* Кольцо фокуса: без него клавиатурная навигация по диалогам невидима. */
QPushButton:focus {{ border-color: {BORDER_BRIGHT}; outline: none; }}
QPushButton#primary:focus {{ border-color: #FFFFFF; }}

QPushButton#primary {{
    background: {ACCENT};
    border: 1px solid rgba(255, 255, 255, 0.18);
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#primary:hover   {{ background: #FF7150; }}
QPushButton#primary:pressed {{ background: {ACCENT_DIM}; }}

QPushButton#danger {{ color: {DANGER}; }}

QPushButton#icon {{
    border-radius: 9px;
    padding: 6px 9px;
    font-size: 14px;
    min-width: 18px;
}}

QLineEdit, QSpinBox, QComboBox, QKeySequenceEdit {{
    background: rgba(0, 0, 0, 0.28);
    border: 1px solid {BORDER};
    border-radius: 9px;
    padding: 6px 9px;
    font-size: 13px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QKeySequenceEdit:focus {{
    border-color: {BORDER_BRIGHT};
}}
QComboBox QAbstractItemView {{
    background: #24242A;
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    outline: none;
}}

QCheckBox {{ font-size: 13px; spacing: 8px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border-radius: 5px;
    border: 1px solid {BORDER_BRIGHT};
    background: rgba(0, 0, 0, 0.25);
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 12px;
    margin-top: 14px;
    padding: 12px;
    font-size: 12px;
    color: {TEXT_DIM};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 4px; }}

QProgressBar {{
    background: rgba(0, 0, 0, 0.3);
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

/* --- окно настроек ------------------------------------------------------ */
QWidget#settings {{ background: {WINDOW}; }}
QWidget#sidebar {{ background: {SIDEBAR}; }}
QScrollArea, QScrollArea > QWidget > QWidget#page {{ background: {WINDOW}; border: none; }}
QLabel#pageTitle {{
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.5px;   /* крупный кегль — плотнее */
}}
QLabel#sectionTitle {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.6px;    /* капитель мелким кеглем — свободнее */
    color: {TEXT_DIM};
}}
QLabel#rowTitle {{ font-size: 13px; }}
QLabel#rowNote {{ font-size: 12px; color: {TEXT_DIM}; }}
QLabel#rowNote[warn="true"] {{ color: {ACCENT}; }}
QLabel#value {{ font-size: 13px; color: {TEXT_DIM}; }}
QLabel#about {{ font-size: 13px; color: {TEXT_DIM}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {TRACK}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {TEXT_DIM}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{
    background: #24242A;
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 5px 8px;
    color: {TEXT};
}}
"""
