; ─── Clipr Installer Script ────────────────────────────────────
; Inno Setup 6.x

#define MyAppName "Clipr"
; Версию НЕ дублируем: берём из собранного exe, а туда она попадает из
; clipr/constants.py (см. Clipr.spec). Поднять версию = одна строка в constants.
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

[Setup]
AppId={{4E1D9C2A-6B67-4F8E-9E2C-1B0E5A7C33D1}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Папка программ ТЕКУЩЕГО пользователя (%LOCALAPPDATA%\Programs при
; PrivilegesRequired=lowest): туда можно писать без прав администратора, а это
; обязательное условие — Clipr обновляет себя, подменяя exe на месте. В Program
; Files подмена молча не пройдёт.
DefaultDirName={autopf}\{#MyAppName}
; Страницу выбора папки показываем всегда (по умолчанию она прячется при
; установке поверх существующей).
DisableDirPage=no
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=Clipr-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
SetupIconFile=assets\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Code]
// Проверяем, что в выбранную папку можно писать БЕЗ прав администратора: иначе
// самообновление потом будет молча отказывать.
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

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
english.DirNotWritable=This folder cannot be written to without administrator rights.%n%nClipr updates itself and needs write access to its own folder, so please pick another location — for example the default one.
russian.DirNotWritable=В эту папку нельзя записывать без прав администратора.%n%nClipr обновляется самостоятельно и должен иметь доступ на запись в свою папку, поэтому выберите другое место — например, предложенное по умолчанию.
english.AutostartTask=Start Clipr with Windows
russian.AutostartTask=Запускать Clipr вместе с Windows

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "autostart"; Description: "{cm:AutostartTask}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "Clipr"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
