"""Строки интерфейса на двух языках (как в Spotty).

`tr("ключ", имя=значение)` — строка на текущем языке с подстановкой {имя}.
Основной язык — английский, русский — второй. Пропущенный перевод подменяется
английским, а не падает; неизвестный ключ возвращается как есть — так забытая
строка видна сразу, а не прячется.
"""

LANGUAGES = {"en": "English", "ru": "Русский"}
DEFAULT = "en"

_language = DEFAULT

STRINGS = {
    # --- трей ---------------------------------------------------------------- #
    "tray.show_panel":   {"en": "Show Panel", "ru": "Показать панель"},
    "tray.start":        {"en": "Start Recording", "ru": "Начать запись"},
    "tray.stop":         {"en": "Stop Recording", "ru": "Остановить запись"},
    "tray.region":       {"en": "Select Area…", "ru": "Выбрать область…"},
    "tray.full_screen":  {"en": "Record Full Screen", "ru": "Записывать весь экран"},
    "tray.folder":       {"en": "Open Folder", "ru": "Открыть папку"},
    "tray.settings":     {"en": "Settings", "ru": "Настройки"},
    "tray.update":       {"en": "Check for Updates", "ru": "Проверить обновления"},
    "tray.restart":      {"en": "Restart to Update to {version}",
                          "ru": "Перезапустить и обновить до {version}"},
    "tray.quit":         {"en": "Quit", "ru": "Выход"},

    # --- уведомления --------------------------------------------------------- #
    "notify.ready":      {"en": "Version {version} is downloaded. Click to restart.",
                          "ru": "Версия {version} скачана. Нажмите, чтобы перезапустить."},
    "notify.available":  {"en": "Version {version} is available. Click to download.",
                          "ru": "Доступна версия {version}. Нажмите, чтобы скачать."},
    "notify.available_dev": {
        "en": "Version {version} is available. Updates aren't installed when running "
              "from source.",
        "ru": "Доступна версия {version}. В режиме разработки обновление не ставится."},
    "notify.current":    {"en": "You're up to date ({version}).",
                          "ru": "Установлена последняя версия ({version})."},
    "notify.check_failed": {"en": "Couldn't check for updates — no connection to GitHub.",
                            "ru": "Не удалось проверить обновления — нет связи с GitHub."},
    "notify.download_failed": {"en": "Couldn't download the update.",
                               "ru": "Не удалось скачать обновление."},
    "notify.stop_first": {"en": "Stop the recording first, then restart.",
                          "ru": "Сначала остановите запись — потом перезапуск."},
    "notify.install_failed": {"en": "Couldn't install the update — download it again.",
                              "ru": "Не удалось установить обновление — скачайте его заново."},
    "notify.nothing":    {"en": "Nothing was recorded.", "ru": "Ничего не записалось."},
    "notify.copied":     {"en": "GIF copied — paste it with Ctrl+V.",
                          "ru": "GIF скопирован — вставьте его через Ctrl+V."},
    "toast.copied":      {"en": "GIF copied to clipboard", "ru": "GIF скопирован в буфер"},
    "toast.copied_sub":  {"en": "Paste with Ctrl+V · {size} · click to show",
                          "ru": "Вставьте через Ctrl+V · {size} · клик — показать"},
    "size.kb":           {"en": "{value} KB", "ru": "{value} КБ"},
    "size.mb":           {"en": "{value} MB", "ru": "{value} МБ"},
    "ffmpeg.missing_title": {"en": "FFmpeg Not Found", "ru": "FFmpeg не найден"},
    "ffmpeg.missing_text": {
        "en": "Clipr can't record or build GIFs without FFmpeg.\n\n"
              "Download it now (about 80 MB, into the app folder in AppData)?",
        "ru": "Clipr не может записывать и собирать GIF без FFmpeg.\n\n"
              "Скачать его сейчас (около 80 МБ, в папку программы в AppData)?"},

    # --- панель записи ------------------------------------------------------- #
    "panel.full_screen": {"en": "Full Screen", "ru": "Весь экран"},
    "panel.region":      {"en": "Select recording area", "ru": "Выбрать область записи"},
    "panel.reset_region": {"en": "Back to full screen", "ru": "Вернуть весь экран"},
    "panel.record":      {"en": "Record", "ru": "Запись"},
    "panel.record_tip":  {"en": "Start recording", "ru": "Начать запись"},
    "panel.pause":       {"en": "Pause", "ru": "Пауза"},
    "panel.resume":      {"en": "Resume", "ru": "Продолжить"},
    "panel.stop":        {"en": "Stop and edit", "ru": "Стоп и редактирование"},
    "panel.folder":      {"en": "Open recordings folder", "ru": "Открыть папку с записями"},
    "panel.settings":    {"en": "Settings", "ru": "Настройки"},
    "panel.close":       {"en": "Hide panel (click the tray icon to bring it back)",
                          "ru": "Скрыть панель (вернуть — клик по иконке в трее)"},

    # --- выбор области ------------------------------------------------------- #
    "select.hint": {"en": "Drag to select an area  ·  Click for the whole screen  ·  "
                          "Esc to cancel",
                    "ru": "Протяните рамку  ·  Клик — весь экран  ·  Esc — отмена"},

    # --- запись и экспорт ---------------------------------------------------- #
    "rec.no_ffmpeg":     {"en": "FFmpeg not found. Install it in Settings.",
                          "ru": "FFmpeg не найден. Установите его в настройках."},
    "rec.too_small":     {"en": "The recording area is too small.",
                          "ru": "Область записи слишком мала."},
    "rec.not_started":   {"en": "FFmpeg didn't start.", "ru": "FFmpeg не запустился."},
    "rec.cant_start":    {"en": "Couldn't start FFmpeg.", "ru": "Не удалось запустить FFmpeg."},
    "rec.concat_failed": {"en": "Couldn't join the recording segments.",
                          "ru": "Не удалось склеить сегменты записи."},
    "gif.palette":       {"en": "Building palette…", "ru": "Строим палитру…"},
    "gif.building":      {"en": "Building GIF…", "ru": "Собираем GIF…"},
    "gif.compressing":   {"en": "Compressing…", "ru": "Сжимаем…"},
    "gif.done":          {"en": "Done", "ru": "Готово"},
    "gif.palettegen_failed": {"en": "palettegen failed.", "ru": "Ошибка palettegen."},
    "gif.paletteuse_failed": {"en": "paletteuse failed.", "ru": "Ошибка paletteuse."},
    "gif.timeout":       {"en": "FFmpeg didn't respond in time.",
                          "ru": "FFmpeg не ответил вовремя."},

    # --- редактор ------------------------------------------------------------ #
    "editor.title":      {"en": "Clipr — Edit Recording", "ru": "Clipr — редактирование записи"},
    "editor.heading":    {"en": "Edit Recording", "ru": "Редактирование записи"},
    "editor.pause":      {"en": "Pause (Space)", "ru": "Пауза (Пробел)"},
    "editor.play":       {"en": "Play (Space)", "ru": "Играть (Пробел)"},
    "editor.crop_none":  {"en": "Crop: full frame", "ru": "Кадрирование: весь кадр"},
    "editor.crop":       {"en": "Crop: {w}×{h} @ {x},{y}", "ru": "Кадр: {w}×{h} @ {x},{y}"},
    "editor.reset_crop": {"en": "Reset Crop", "ru": "Сбросить кадр"},
    "editor.reset_crop_tip": {"en": "Remove crop (R)", "ru": "Убрать кадрирование (R)"},
    "editor.size":       {"en": "Size", "ru": "Размер"},
    "editor.fps":        {"en": "FPS", "ru": "Кадры/с"},
    "editor.compress":   {"en": "Compression", "ru": "Сжатие"},
    "editor.compress_tip": {"en": "gifsicle with loss: smaller file, noise on flat areas",
                            "ru": "gifsicle с потерями: файл меньше, на заливках шум"},
    "editor.open_folder": {"en": "Open Folder", "ru": "Открыть папку"},
    "editor.export":     {"en": "Export GIF", "ru": "Экспорт GIF"},
    "editor.export_tip": {"en": "Build the GIF and copy it (Enter)",
                          "ru": "Собрать GIF и скопировать (Enter)"},
    "editor.exporting":  {"en": "Exporting…", "ru": "Собираем…"},
    "editor.copied":     {"en": "Copied", "ru": "Скопировано"},
    "editor.selected":   {"en": "{position} / selected {length}",
                          "ru": "{position} / выбрано {length}"},
    "editor.result":     {"en": "output: {w}×{h} · {seconds:.1f} s",
                          "ru": "итог: {w}×{h} · {seconds:.1f} с"},
    "editor.saved":      {"en": "Saved {name} · {size} · in the clipboard",
                          "ru": "Сохранено {name} · {size} · в буфере обмена"},
    "editor.preview_error": {"en": "Preview error: {message}",
                             "ru": "Ошибка предпросмотра: {message}"},
    "editor.failed":     {"en": "Export Failed", "ru": "Экспорт не удался"},

    # --- настройки: общее ---------------------------------------------------- #
    "settings.title":    {"en": "Settings", "ru": "Настройки"},
    "settings.window":   {"en": "Clipr Settings", "ru": "Clipr — настройки"},
    "nav.capture":       {"en": "Recording", "ru": "Запись"},
    "nav.gif":           {"en": "GIF", "ru": "GIF"},
    "nav.keys":          {"en": "Shortcuts", "ru": "Клавиши"},
    "nav.files":         {"en": "Files", "ru": "Файлы"},
    "nav.system":        {"en": "System", "ru": "Система"},
    "nav.about":         {"en": "About", "ru": "О программе"},
    "choose_folder":     {"en": "Choose Folder", "ru": "Выберите папку"},

    # запись
    "capture.section":   {"en": "Capture", "ru": "Захват"},
    "capture.fps":       {"en": "Recording frame rate", "ru": "Частота кадров записи"},
    "capture.fps_value": {"en": "{value} fps", "ru": "{value} к/с"},
    "capture.fps_note":  {"en": "The GIF has its own frame rate — the extra frames make "
                                "trimming precise",
                          "ru": "GIF всё равно собирается со своей частотой — запас нужен "
                                "для плавной обрезки"},
    "capture.backend":   {"en": "Capture method", "ru": "Способ захвата"},
    "capture.backend_note": {
        "en": "Auto uses Desktop Duplication when the area fits on one monitor — the "
              "cursor doesn't flicker. GDI works everywhere, but the cursor blinks while "
              "recording",
        "ru": "«Авто» снимает через Desktop Duplication, если область на одном мониторе, — "
              "курсор не мигает. GDI работает везде, но курсор во время записи моргает"},
    "capture.auto":      {"en": "Auto", "ru": "Авто"},
    "capture.cursor":    {"en": "Record the cursor", "ru": "Записывать курсор"},

    # gif
    "gif.section_defaults": {"en": "Defaults", "ru": "По умолчанию"},
    "gif.size":          {"en": "Size", "ru": "Размер"},
    "gif.size_note":     {"en": "Share of the selected area: {w}×{h} → {ow}×{oh}",
                          "ru": "Доля от выделенной области: {w}×{h} → {ow}×{oh}"},
    "gif.fps":           {"en": "Frames per second", "ru": "Кадры в секунду"},
    "gif.fps_note":      {"en": "Fewer frames, smaller file; 15 is plenty for UI",
                          "ru": "Меньше кадров — меньше файл; 15 хватает для интерфейсов"},
    "gif.dither":        {"en": "Color dithering", "ru": "Сглаживание цветов"},
    "gif.dither_note":   {"en": "GIF has only 256 colors. Pattern is cleaner on UI, "
                                "Diffusion on gradients and photos",
                          "ru": "У GIF всего 256 цветов. «Сетка» чище на интерфейсах, "
                                "«Диффузия» — на градиентах и фото"},
    "dither.bayer":      {"en": "Pattern", "ru": "Сетка"},
    "dither.diffusion":  {"en": "Diffusion", "ru": "Диффузия"},
    "dither.none":       {"en": "None", "ru": "Без"},
    "gif.section_compress": {"en": "Compression", "ru": "Сжатие"},
    "gif.compress":      {"en": "Lossy compression", "ru": "Сжатие с потерями"},
    "compress.none":     {"en": "Off", "ru": "Нет"},
    "compress.light":    {"en": "Light", "ru": "Лёгкое"},
    "compress.strong":   {"en": "Strong", "ru": "Сильное"},
    "gif.compress_explain": {
        "en": "Compression is done by gifsicle. Lossless, it gains almost nothing — FFmpeg "
              "already writes only the changed parts of each frame — while lossy roughly "
              "halves the file but adds noise to flat areas.",
        "ru": "Сжатие делает gifsicle: без потерь он почти ничего не выигрывает — FFmpeg "
              "уже пишет только изменившиеся части кадров, — а с потерями уменьшает файл "
              "примерно вдвое, но добавляет «шум» на ровных заливках."},
    "compress.off_note": {"en": "Off: the GIF stays pristine", "ru": "Выключено: GIF без искажений"},
    "compress.downloading": {"en": "Downloading gifsicle…", "ru": "Скачивается gifsicle…"},
    "compress.missing":  {"en": "gifsicle isn't installed — the GIF will be built without "
                                "compression",
                          "ru": "gifsicle не установлен — GIF соберётся без сжатия"},
    "compress.light_note": {"en": "Noticeably smaller, slight noise on flat areas",
                            "ru": "Файл заметно меньше, на заливках появится лёгкий шум"},
    "compress.strong_note": {"en": "About half the size, noise is visible",
                             "ru": "Файл примерно вдвое меньше, шум заметен"},

    # клавиши
    "keys.section":      {"en": "Work in every app", "ru": "Работают во всех программах"},
    "keys.record":       {"en": "Start / stop recording", "ru": "Начать / остановить запись"},
    "keys.pause":        {"en": "Pause / resume", "ru": "Пауза / продолжить"},
    "keys.region":       {"en": "Select area", "ru": "Выбрать область"},
    "keys.panel":        {"en": "Show / hide panel", "ru": "Показать / скрыть панель"},
    "keys.help":         {"en": "Click a field and press a shortcut. It needs Ctrl, Alt or "
                                "Win — otherwise Clipr would catch ordinary typing. "
                                "Backspace clears, Esc cancels.",
                          "ru": "Щёлкните поле и нажмите сочетание. Нужен Ctrl, Alt или Win — "
                                "иначе Clipr перехватывал бы обычный набор текста. "
                                "Backspace — убрать, Esc — отменить."},
    "keys.duplicate":    {"en": "Already used for “{title}”", "ru": "Уже назначено: «{title}»"},
    "keys.taken":        {"en": "Taken by another app — pick a different one",
                          "ru": "Занято другой программой — выберите другое"},
    "keys.press":        {"en": "Press a shortcut…", "ru": "Нажмите сочетание…"},
    "keys.none":         {"en": "Not set", "ru": "Не задано"},

    # файлы
    "files.section":     {"en": "Folders", "ru": "Папки"},
    "files.output":      {"en": "Finished GIFs", "ru": "Готовые GIF"},
    "files.temp":        {"en": "Temporary recordings", "ru": "Временные записи"},
    "files.temp_note":   {"en": "{path} · cleaned up after a day",
                          "ru": "{path} · чистится сама через сутки"},
    "files.change":      {"en": "Change…", "ru": "Изменить…"},
    "files.open":        {"en": "Open", "ru": "Открыть"},
    "files.keep":        {"en": "Keep the source video", "ru": "Сохранять исходное видео"},
    "files.keep_note":   {"en": "A lossless MP4 is saved next to the GIF — rebuild the GIF "
                                "from it later with other settings",
                          "ru": "MP4 без потерь кладётся рядом с GIF — из него можно собрать "
                                "GIF заново с другими настройками"},
    "files.tools":       {"en": "External tools", "ru": "Внешние программы"},
    "files.tools_folder": {"en": "Open Tools Folder", "ru": "Открыть папку программ"},
    "tool.downloading":  {"en": "Downloading…", "ru": "Скачивается…"},
    "tool.failed":       {"en": "Couldn't download: {error}", "ru": "Не удалось скачать: {error}"},
    "tool.installed":    {"en": "Installed · {where}", "ru": "Установлен · {where}"},
    "tool.own_copy":     {"en": "own copy", "ru": "своя копия"},
    "tool.missing_ffmpeg": {"en": "Not found — Clipr can't record without it",
                            "ru": "Не найден — без него Clipr не запишет экран"},
    "tool.missing_gifsicle": {"en": "Not found — only needed for compression",
                              "ru": "Не найден — нужен только для сжатия"},
    "tool.update":       {"en": "Update", "ru": "Обновить"},
    "tool.get_ffmpeg":   {"en": "Download · 80 MB", "ru": "Скачать · 80 МБ"},
    "tool.get_gifsicle": {"en": "Download · 230 KB", "ru": "Скачать · 230 КБ"},

    # система
    "system.section_start": {"en": "Startup", "ru": "Запуск"},
    "system.autostart":  {"en": "Launch with Windows", "ru": "Запускать вместе с Windows"},
    "system.autostart_dev": {"en": "Works in the installed version",
                             "ru": "Работает в установленной версии"},
    "system.minimized":  {"en": "Start minimized", "ru": "Запускать свёрнутой"},
    "system.minimized_note": {"en": "Clipr starts in the tray without the recording panel — "
                                    "show it with {shortcut} or a click on the icon",
                              "ru": "Clipr стартует в трее без панели записи — панель по "
                                    "{shortcut} или клику по значку"},
    "system.tray_icon":  {"en": "the tray icon", "ru": "значку в трее"},
    "system.section_language": {"en": "Language", "ru": "Язык"},
    "system.language":   {"en": "Interface language", "ru": "Язык интерфейса"},
    "system.section_motion": {"en": "Motion", "ru": "Движение"},
    "system.reduced":    {"en": "Reduce motion", "ru": "Меньше анимации"},
    "system.reduced_note": {"en": "Shorter transitions without movement; fades stay",
                            "ru": "Появления и сдвиги короче; плавная смена прозрачности "
                                  "остаётся"},
    "system.reduced_os": {"en": "Animations are already off in Windows settings — Clipr "
                                "follows them",
                          "ru": "Анимация уже выключена в параметрах Windows — Clipr "
                                "следует им"},

    # о программе
    "about.tagline":     {"en": "Version {version} · screen to GIF",
                          "ru": "Версия {version} · запись экрана в GIF"},
    "about.section_updates": {"en": "Updates", "ru": "Обновления"},
    "about.check":       {"en": "Check for updates", "ru": "Проверять обновления"},
    "about.check_note":  {"en": "Every few hours, quietly — you only hear about something new",
                          "ru": "Раз в несколько часов, тихо — сообщение только если есть "
                                "новое"},
    "about.background":  {"en": "Download in background", "ru": "Скачивать в фоне"},
    "about.background_note": {"en": "A found update downloads by itself; restart when it "
                                    "suits you",
                              "ru": "Найденное обновление скачается само; перезапуск — когда "
                                    "удобно"},
    "about.update":      {"en": "Update", "ru": "Обновление"},
    "about.section_project": {"en": "Project", "ru": "Проект"},
    "about.whats_new":   {"en": "What's new", "ru": "Что нового"},
    "about.whats_new_note": {"en": "Release notes on GitHub", "ru": "Список изменений на GitHub"},
    "about.developer":   {"en": "Developer", "ru": "Разработчик"},
    "update.idle":       {"en": "Clipr {version}", "ru": "Clipr {version}"},
    "update.checking":   {"en": "Checking…", "ru": "Проверяю…"},
    "update.current":    {"en": "You're up to date", "ru": "У вас последняя версия"},
    "update.available":  {"en": "Version {version} is available", "ru": "Доступна версия {version}"},
    "update.downloading": {"en": "Downloading {version}…", "ru": "Скачиваю {version}…"},
    "update.ready":      {"en": "Version {version} is downloaded — just restart",
                          "ru": "Версия {version} скачана — осталось перезапустить"},
    "update.error":      {"en": "Couldn't check — no connection to GitHub",
                          "ru": "Не удалось проверить — нет связи с GitHub"},
    "update.failed":     {"en": "Couldn't download the update",
                          "ru": "Не удалось скачать обновление"},
    "update.dev":        {"en": "{version} is available — installs only in the built app",
                          "ru": "Доступна {version} — ставится только в собранной версии"},
    "update.check_button": {"en": "Check", "ru": "Проверить"},
    "update.install_button": {"en": "Download and Install", "ru": "Скачать и установить"},
    "update.restart_button": {"en": "Restart", "ru": "Перезапустить"},
}


def set_language(code):
    """Неизвестный код (например, из старых настроек) — английский."""
    global _language
    _language = code if code in LANGUAGES else DEFAULT


def language():
    return _language


def size_text(size):
    """Байты — в КБ до мегабайта и в МБ дальше; у русских — десятичная запятая."""
    if size < 1024 * 1024:
        text = tr("size.kb", value=max(1, round(size / 1024)))
    else:
        text = tr("size.mb", value="%.1f" % (size / 1024 / 1024))
    return text.replace(".", ",") if _language == "ru" else text


def tr(key, **kwargs):
    entry = STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(_language) or entry.get("en") or key
    return text.format(**kwargs) if kwargs else text
