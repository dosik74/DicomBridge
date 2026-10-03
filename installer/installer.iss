; DicomBridge installer (Inno Setup 6). Russian wizard, modern style.
; Build: iscc installer\installer.iss   (output -> setup\DicomBridge-Setup.exe)

#define MyAppName "DicomBridge"
#define MyAppVersion "1.0.1"
#define MyAppPublisher "dosik74"
#define MyAppURL "https://github.com/dosik74/DicomBridge"

[Setup]
AppId={{10F9A91E-09DC-410A-9C62-633BDE44CFD7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
LicenseFile=..\LICENSE.txt
SetupIconFile=app.ico
WizardStyle=modern
WizardSizePercent=120
WizardImageFile=wizard.png
WizardSmallImageFile=header.png
Compression=lzma2/max
SolidCompression=yes
OutputDir=..\setup
OutputBaseFilename=DicomBridge-Setup
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#MyAppName} {#MyAppVersion}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Значки:"; Flags: unchecked
Name: "autostart"; Description: "Запускать вместе с Windows (свёрнутым в трей)"; GroupDescription: "Автозапуск:"; Flags: unchecked

[Files]
Source: "..\dist\DicomBridge.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\DicomBridge"; Filename: "{app}\DicomBridge.exe"
Name: "{group}\Удалить DicomBridge"; Filename: "{uninstallexe}"
Name: "{autodesktop}\DicomBridge"; Filename: "{app}\DicomBridge.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "DicomBridge"; ValueData: """{app}\DicomBridge.exe"" --minimized"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\DicomBridge.exe"; Description: "Запустить DicomBridge"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  { закрыть работающую копию, иначе файлы заблокированы }
  Exec('taskkill.exe', '/F /IM DicomBridge.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;
