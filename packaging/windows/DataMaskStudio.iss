#ifndef MyAppVersion
  #error MyAppVersion must be supplied by build_windows.ps1
#endif
#ifndef BuildRoot
  #error BuildRoot must be supplied by build_windows.ps1
#endif
#ifndef ReleaseRoot
  #error ReleaseRoot must be supplied by build_windows.ps1
#endif
#ifndef DmsIconFile
  #error DmsIconFile must point to the official DMS icon
#endif

#define MyAppName "Data Mask Studio"
#define MyAppExeName "DataMaskStudio.exe"

[Setup]
AppId={{D5C24B6C-16B7-4CF5-9D92-5405A6974D43}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Data Mask Studio
VersionInfoVersion={#MyAppVersion}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoDescription=Data Mask Studio Setup
VersionInfoProductName=Data Mask Studio
DefaultDirName={localappdata}\Programs\Data Mask Studio
DefaultGroupName=Data Mask Studio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#ReleaseRoot}
OutputBaseFilename=DataMaskStudio-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
SetupMutex=DataMaskStudio-Setup-Mutex-D5C24B6C
UsePreviousAppDir=yes
SetupIconFile={#DmsIconFile}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar um atalho na área de trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

[Files]
Source: "{#BuildRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Data Mask Studio"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Data Mask Studio"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir Data Mask Studio"; Flags: nowait postinstall skipifsilent

; Não há seção [UninstallDelete] para %LOCALAPPDATA%\DataMaskStudio.
; O cofre, as chaves, os perfis e as configurações locais são preservados.
