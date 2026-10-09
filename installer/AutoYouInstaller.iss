; AutoYou Windows Installer (Inno Setup Script)
; Packages the published Windows desktop distribution from
; servers\windows\dist\AutoYou-win-x64.

#ifndef MyAppName
	#define MyAppName "AutoYou"
#endif

#ifndef MyAppVersion
	#define MyAppVersion "81.0.2"
#endif

#ifndef MyPublisher
	#define MyPublisher "OpenStorey LLC"
#endif

#ifndef MyURL
	#define MyURL "https://www.autoyou.me/"
#endif

#ifndef MyAppExeName
	#define MyAppExeName "AutoYou.exe"
#endif

#ifndef MyDistDir
	#define MyDistDir "..\servers\windows\dist\AutoYou-win-x64"
#endif

#ifndef MyOutputDir
	#define MyOutputDir "..\servers\windows\dist\release"
#endif

#ifndef MyOutputBaseFilename
	#define MyOutputBaseFilename "AutoYouSetup-win-x64"
#endif

[Setup]
AppId={{A3E9D9AB-0D12-4DBA-9C9B-FA2D6BFC979E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyPublisher}
AppPublisherURL={#MyURL}
AppSupportURL=https://www.autoyou.me/support/
AppUpdatesURL=https://www.autoyou.me/
LicenseFile={#MyDistDir}\Legal\LICENSE
; Keep the default path short: connector-full builds include deeply nested
; Python package files that can exceed legacy MAX_PATH under longer roots.
DefaultDirName={localappdata}\AY
; Keep the destination step visible so large local runtimes can be placed on D:.
DisableDirPage=no
DefaultGroupName={#MyAppName}
AllowNoIcons=no
OutputDir={#MyOutputDir}
OutputBaseFilename={#MyOutputBaseFilename}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
; Include the entire published desktop distribution.
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\AutoYou"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\AutoYou"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch AutoYou"; Flags: postinstall nowait skipifsilent
