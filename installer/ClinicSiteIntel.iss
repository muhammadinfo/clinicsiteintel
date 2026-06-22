; Inno Setup script — produces a single ClinicSiteIntel-Setup.exe installer.
; Requires Inno Setup (free): https://jrsoftware.org/isdl.php
; Build the PyInstaller output FIRST (run build_exe.ps1), then compile this
; script with the Inno Setup Compiler (ISCC.exe) or the IDE "Compile" button.

#define MyAppName "ClinicSiteIntel"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Facial Pain LLC / Advanced Dental Sleep & TMJ Clinic"
#define MyAppExeName "ClinicSiteIntel.exe"

[Setup]
AppId={{8E2B6F2E-2B3A-4D2E-9B1C-CLINICSITEINTEL}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename=ClinicSiteIntel-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\dist\ClinicSiteIntel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
