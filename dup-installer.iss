[Setup]
AppName=Duplicate File Finder
AppVersion=1.4
AppPublisher=mPhpMaster
AppPublisherURL=https://github.com/mPhpMaster/duplicate-finder-app
AppSupportURL=https://github.com/mPhpMaster/duplicate-finder-app
AppCopyright=Copyright © 2026 mPhpMaster
DefaultDirName={pf}\Duplicate File Finder
DefaultGroupName=Duplicate File Finder
DisableProgramGroupPage=no
AllowNoIcons=yes
LicenseFile=LICENSE
OutputBaseFilename=DuplicateFileFinder-Setup
OutputDir=dist
Compression=lzma
SolidCompression=yes
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\Duplicate File Finder.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\Duplicate File Finder.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "duplicate_finder_settings.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Duplicate File Finder"; Filename: "{app}\Duplicate File Finder.exe"
Name: "{userdesktop}\Duplicate File Finder"; Filename: "{app}\Duplicate File Finder.exe"

[Run]
Filename: "{app}\Duplicate File Finder.exe"; Description: "Launch Duplicate File Finder"; Flags: nowait postinstall skipifsilent
