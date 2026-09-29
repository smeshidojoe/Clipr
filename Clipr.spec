# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller --clean --noconfirm Clipr.spec  ->  dist\Clipr.exe
#
# Один exe (как Spotty и Scribe): самообновление подменяет ровно один файл.
# FFmpeg и gifsicle в exe не кладём — они качаются из программы в
# %APPDATA%\Clipr\tools или берутся из PATH.
#
# Собираем из глобального Python, не из venv: pip install -r requirements.txt pyinstaller

import importlib.util
import re
import sys

# --- проверка окружения ------------------------------------------------------ #
# Модуля нет в Python, которым запущен PyInstaller, — он лишь пишет строчку в
# warn-Clipr.txt и собирает exe, который падает при запуске. Такую сборку
# обрываем сразу.
_NEED = ['PySide6', 'certifi']
_MISSING = [m for m in _NEED if importlib.util.find_spec(m) is None]
if _MISSING:
    raise SystemExit(
        '\nСборка остановлена: в %s нет модулей %s.\n'
        'Поставьте их в глобальный Python:\n'
        '  pip install -r requirements.txt pyinstaller\n'
        '  pyinstaller --clean --noconfirm Clipr.spec\n'
        % (sys.executable, ', '.join(_MISSING)))

# --- версия ----------------------------------------------------------------- #
# Единственный источник — clipr/constants.py. Отдельный version_info.txt не
# заводим: он дублировал бы ту же строку и однажды разошёлся бы с реальностью.
# Установщик берёт версию уже из готового exe.
_APP_VERSION = re.search(
    r'APP_VERSION\s*=\s*"([^"]+)"',
    open('clipr/constants.py', encoding='utf-8').read()).group(1)
_VER_TUPLE = tuple(int(x) for x in (_APP_VERSION.split('.') + ['0'] * 4)[:4])

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo,
    VarStruct, VSVersionInfo)

_VERSION_RES = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_VER_TUPLE, prodvers=_VER_TUPLE,
                      mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', 'SmeshidoJoe'),
            StringStruct('FileDescription', 'Clipr'),
            StringStruct('FileVersion', _APP_VERSION),
            StringStruct('InternalName', 'Clipr'),
            StringStruct('LegalCopyright', '© 2026 SmeshidoJoe'),
            StringStruct('OriginalFilename', 'Clipr.exe'),
            StringStruct('ProductName', 'Clipr'),
            StringStruct('ProductVersion', _APP_VERSION),
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ])

# Иконки трея и окон рисуются кодом (clipr/ui/icons.py), поэтому assets в exe
# не нужны — app.ico идёт только в ресурс самого exe (icon= ниже).
_DATAS = []

# certifi импортируется внутри функции (clipr.tools.ssl_context) — указываем
# явно, иначе связка корневых сертификатов не попадёт в сборку, и загрузка
# FFmpeg/gifsicle/обновлений упадёт на устаревшем системном корне.
_HIDDEN = ['certifi']

# В глобальном окружении обычно стоит всё подряд: другие привязки Qt (PyInstaller
# не умеет класть два набора сразу и обрывает сборку), numpy, Pillow и прочее.
# Clipr ничего из этого не нужно.
_EXCLUDES = [
    'PyQt5', 'PyQt6', 'PySide2', 'shiboken2', 'qtpy', 'sip',
    'matplotlib', 'tkinter', '_tkinter', 'IPython', 'jedi', 'notebook', 'pytest',
    'PIL', 'numpy', 'pandas', 'scipy', 'cv2', 'torch',
    # Куски Qt, которых нет на экране. QtMultimedia остаётся: им редактор
    # проигрывает запись.
    'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml',
    'PySide6.QtQuick', 'PySide6.QtQuickWidgets', 'PySide6.Qt3DCore',
    'PySide6.QtCharts', 'PySide6.QtDataVisualization', 'PySide6.QtBluetooth',
    'PySide6.QtSql', 'PySide6.QtTest', 'PySide6.QtNetworkAuth', 'PySide6.QtPdf',
    'PySide6.QtSvg', 'PySide6.QtOpenGL', 'PySide6.QtOpenGLWidgets', 'PySide6.QtDesigner',
]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=_DATAS,
    hiddenimports=_HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=_EXCLUDES,
    noarchive=False,
    optimize=0,
)
# Библиотеки, которые приходят вслед за плагинами Qt, а Clipr не нужны (как в Spotty):
#   opengl32sw.dll     — программный OpenGL на 20 МБ для машин без драйвера;
#   Quick/Qml/Pdf/Svg  — с плагинами экранной клавиатуры и форматов картинок;
#   *-x64 OpenSSL, tls — шифрование QtNetwork. Сеть у нас идёт через urllib со
#                        своим OpenSSL (libcrypto-3.dll без -x64), его не трогаем.
_DROP = ('opengl32sw.dll', 'qt6quick', 'qt6qml', 'qt6pdf', 'qt6svg', 'qt6virtualkeyboard',
         'platforminputcontexts', 'qdirect2d', '\\qpdf', '\\qsvg', 'libcrypto-3-x64',
         'libssl-3-x64', '\\tls\\')
a.binaries = [b for b in a.binaries if not any(d in b[0].lower() for d in _DROP)]
# Переводы стандартных диалогов Qt (6 МБ) — диалог выбора папки и так рисует Windows.
a.datas = [d for d in a.datas if '\\translations\\' not in d[0].lower()]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Clipr',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Рантайм Python и кодеки проигрывателя UPX'ом не жмём: упакованная DLL
    # иногда не грузится, а выигрыш на уже сжатом onefile-архиве мизерный.
    upx_exclude=['vcruntime140.dll', 'vcruntime140_1.dll', 'python313.dll',
                 'avcodec-61.dll', 'avformat-61.dll', 'avutil-59.dll',
                 'swresample-5.dll', 'swscale-8.dll'],
    runtime_tmpdir=None,
    console=False,              # программа живёт в трее, консоль не нужна
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\app.ico'],
    version=_VERSION_RES,
)
