; ─── Clipr Installer Script ────────────────────────────────────
; Inno Setup 6.x

#define MyAppName "Clipr"
; Версию НЕ дублируем: берём из собранного exe, а туда она попадает из
; clipr/constants.py (см. Clipr.spec). Поднять версию = поправить одну
; строку в constants.py и пересобрать.
#define MyAppExe "dist\Clipr.exe"
#if !FileExists(AddBackslash(SourcePath) + MyAppExe)
  #error Сначала соберите exe: pyinstaller --clean --noconfirm Clipr.spec
#endif
#define MyAppVersion GetStringFileInfo(AddBackslash(SourcePath) + MyAppExe, PRODUCT_VERSION)
#if MyAppVersion == ""
  #error В exe нет ресурса версии. Пересоберите его текущей Clipr.spec.
#endif
#define MyAppExeName "Clipr.exe"
#define MyAppPublisher "SmeshidoJoe"
#define MyAppUrl "https://github.com/SmeshidoJoe/Clipr"

[Setup]
; Первая скобка удвоена не по ошибке: одиночная «{» в Inno Setup начинает
; константу, и AppId с GUID без экранирования не компилируется.
AppId={{4E1D9C2A-6B67-4F8E-9E2C-1B0E5A7C33D1}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppUrl}
AppSupportURL={#MyAppUrl}
AppUpdatesURL={#MyAppUrl}/releases

; Ставим в папку программ текущего пользователя: при PrivilegesRequired=lowest
; это %LOCALAPPDATA%\Programs, куда можно писать без прав администратора. Это
; нужно самообновлению, которое подменяет exe на месте — в Program Files
; подмена молча не пройдёт.
DefaultDirName={autopf}\{#MyAppName}
DisableDirPage=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest

OutputDir=installer_output
OutputBaseFilename=Clipr-Setup-{#MyAppVersion}
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

; Clipr держит один экземпляр на именованном мьютексе и сидит в трее. Если его
; не закрыть, установщик не сможет заменить exe. Имя — INSTANCE_MUTEX из
; clipr/constants.py.
CloseApplications=yes
RestartApplications=no
AppMutex=Clipr-Single-Instance-Mutex

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
english.DirNotWritable=This folder cannot be written to without administrator rights.%n%nClipr updates itself and needs write access to its own folder, so please pick another location — for example the default one.
russian.DirNotWritable=В эту папку нельзя записывать без прав администратора.%n%nClipr обновляет себя сам и должен иметь доступ на запись в свою папку, поэтому выберите другое место — например, предложенное по умолчанию.
english.KeepData=Keep settings and downloaded FFmpeg
russian.KeepData=Оставить настройки и скачанный FFmpeg

[Code]
// Проверяем, что в выбранную папку можно писать БЕЗ прав администратора.
// Иначе пользователь выберет Program Files, установка пройдёт, а обновление
// потом будет молча отказывать.
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Probe: string;
begin
  Result := True;
  if CurPageID <> wpSelectDir then
    Exit;
  ForceDirectories(WizardDirValue);
  Probe := AddBackslash(WizardDirValue) + 'clipr_write_test.tmp';
  if SaveStringToFile(Probe, 'x', False) then
    DeleteFile(Probe)
  else begin
    Result := False;
    MsgBox(ExpandConstant('{cm:DirNotWritable}'), mbError, MB_OK);
  end;
end;

// При удалении спрашиваем, оставлять ли данные. Молча стирать нельзя: FFmpeg
// весит под сотню мегабайт и качался долго; молча оставлять мусор — тоже
// некрасиво. Незаконченные записи во временной папке и журнал падений не нужны
// никому — их убираем без вопросов. Готовые GIF (Видео\Clipr) не трогаем
// никогда: это файлы пользователя, а не программы.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  // Временная папка по умолчанию — %TEMP%\Clipr (clipr/config.py). Свою,
  // заданную в настройках, не трогаем: там могут лежать чужие файлы.
  DelTree(ExpandConstant('{localappdata}\Temp\{#MyAppName}'), True, True, True);
  DataDir := ExpandConstant('{userappdata}\{#MyAppName}');
  if not DirExists(DataDir) then
    Exit;
  DelTree(DataDir + '\crash-reports', True, True, True);
  if MsgBox(ExpandConstant('{cm:KeepData}') + '?', mbConfirmation, MB_YESNO) = IDNO then
    DelTree(DataDir, True, True, True);
end;

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
; Clipr живёт в трее и ждёт горячую клавишу, поэтому автозапуск по умолчанию включён.
Name: "startup"; Description: "{cm:AutoStartProgram,{#MyAppName}}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion

; Скачанное, но не установленное обновление программа держит в _update рядом с
; exe. Установщик его не ставил и сам бы не удалил — папка пережила бы удаление.
[UninstallDelete]
Type: filesandordirs; Name: "{app}\_update"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Автозапуск ставится галочкой в мастере. Программа умеет включать и выключать
; его сама из настроек — ключ и значение те же (clipr/autostart.py), поэтому
; они не конфликтуют. Показывать ли панель при старте, решает настройка
; «Запускать свёрнутой», а не строка запуска.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "Clipr"; ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: startup

; Автозапуск, включённый уже в самой программе, установщик не создавал — и без
; этой строки запись пережила бы удаление, а Windows потом каждый раз пыталась
; бы запустить стёртый exe. ValueType: none + dontcreatekey означают «при
; установке ничего не делать», удаление значения происходит только при удалении
; программы.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: none; ValueName: "Clipr"; \
    Flags: dontcreatekey uninsdeletevalue

[Run]
; --show: сразу после установки панель записи открывается, даже если в
; настройках включён запуск свёрнутой, — видно, что всё работает.
Filename: "{app}\{#MyAppExeName}"; Parameters: "--show"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
