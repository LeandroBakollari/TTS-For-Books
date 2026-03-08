#ifndef MyAppName
  #define MyAppName "AudiobookTTS"
#endif

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

[Setup]
AppId={{4BF64CC1-7A10-46A1-8A95-127DD57C7FB4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=ABTTS
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\dist\installer
OutputBaseFilename={#MyAppName}-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\AudiobookTTS\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\AudiobookTTS.exe"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\AudiobookTTS.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\AudiobookTTS.exe"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
