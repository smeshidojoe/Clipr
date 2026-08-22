# -*- mode: python ; coding: utf-8 -*-

# --- версия --------------------------------------------------------------- #
# Единственный источник — clipr/constants.py. Отдельный version_info.txt не
# держим: он дублировал бы ту же строку и однажды разошёлся бы с реальностью.
import re as _re

_APP_VERSION = _re.search(r'APP_VERSION\s*=\s*"([^"]+)"',
                          open('clipr/constants.py', encoding='utf-8').read()).group(1)
_VER_TUPLE = tuple(int(x) for x in (_APP_VERSION.split('.') + ['0', '0', '0', '0'])[:4])

from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct)

_VERSION_RES = VSVersionInfo(
    ffi=FixedFileInfo(filevers=_VER_TUPLE, prodvers=_VER_TUPLE,
                      mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('CompanyName', 'SmeshidoJoe'),
            StringStruct('FileDescription', 'Clipr — запись экрана в GIF'),
            StringStruct('FileVersion', _APP_VERSION),
            StringStruct('InternalName', 'Clipr'),
            StringStruct('LegalCopyright', '© 2026 SmeshidoJoe'),
            StringStruct('OriginalFilename', 'Clipr.exe'),
            StringStruct('ProductName', 'Clipr'),
            StringStruct('ProductVersion', _APP_VERSION),
        ])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])]),
    ])

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets', 'assets')],
    # certifi импортируется внутри функции (clipr.tools.ssl_context) — указываем
    # явно, иначе связка корневых сертификатов не попадёт в сборку, и загрузка
    # gifsicle упадёт на просроченном системном корне.
    hiddenimports=['certifi'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Тянуть весь Qt незачем: используем только эти модули.
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
              'PySide6.Qt3DCore', 'PySide6.QtQuick', 'PySide6.QtQml',
              'PySide6.QtCharts', 'PySide6.QtDataVisualization',
              'tkinter', 'unittest', 'pydoc_data'],
    noarchive=False,
    optimize=0,
)
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
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\app.ico'],
    version=_VERSION_RES,      # см. _APP_VERSION выше — читается из constants.py
)
