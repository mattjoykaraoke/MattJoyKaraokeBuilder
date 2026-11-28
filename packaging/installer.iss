; Matt Joy Karaoke Builder — Inno Setup Installer Script
; Version is passed automatically by build_installer.bat via /DMyAppVersion=X.Y.Z

#ifndef MyAppVersion
  #define MyAppVersion "1.1.3"
#endif

#define MyAppName "Matt Joy Karaoke Builder"
#define MyAppPublisher "Matt Joy"
#define MyAppURL "https://github.com/MattJoyKaraoke"
#define MyAppExeName "MattJoyKaraokeBuilder.exe"

[Setup]
AppId={{8E4C7B2A-6F1D-4A9E-B3C5-7D2E9F4A1C80}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
LicenseFile=..\LICENSE
OutputDir=..\release
OutputBaseFilename=MattJoyKaraokeBuilder_Setup_v{#MyAppVersion}
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "assoc_mjkp"; Description: "Associate .mjkp project files with {#MyAppName}"; GroupDescription: "File Associations:"
Name: "assoc_kbp"; Description: "Associate .kbp project files with {#MyAppName}"; GroupDescription: "File Associations:"; Flags: unchecked

[Files]
Source: "..\dist\MattJoyKaraokeBuilder\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\mjkp_icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; .mjkp file association
Root: HKA; Subkey: "Software\Classes\.mjkp"; ValueType: string; ValueName: ""; ValueData: "MattJoyKaraokeProject"; Flags: uninsdeletevalue; Tasks: assoc_mjkp
Root: HKA; Subkey: "Software\Classes\MattJoyKaraokeProject"; ValueType: string; ValueName: ""; ValueData: "Matt Joy Karaoke Project"; Flags: uninsdeletekey; Tasks: assoc_mjkp
Root: HKA; Subkey: "Software\Classes\MattJoyKaraokeProject\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\mjkp_icon.ico,0"; Tasks: assoc_mjkp
Root: HKA; Subkey: "Software\Classes\MattJoyKaraokeProject\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: assoc_mjkp

; Optional .kbp file association
Root: HKA; Subkey: "Software\Classes\.kbp"; ValueType: string; ValueName: ""; ValueData: "MattJoyKaraokeKBP"; Flags: uninsdeletevalue; Tasks: assoc_kbp
Root: HKA; Subkey: "Software\Classes\MattJoyKaraokeKBP"; ValueType: string; ValueName: ""; ValueData: "Karaoke Builder Project"; Flags: uninsdeletekey; Tasks: assoc_kbp
Root: HKA; Subkey: "Software\Classes\MattJoyKaraokeKBP\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"; Tasks: assoc_kbp
Root: HKA; Subkey: "Software\Classes\MattJoyKaraokeKBP\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Tasks: assoc_kbp

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
