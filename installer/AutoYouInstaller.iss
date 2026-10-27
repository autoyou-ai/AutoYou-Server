; AutoYou Windows Installer (Inno Setup Script)
; Packages the published Windows desktop distribution from
; servers\windows\dist\AutoYou-win-x64.

#ifndef MyAppName
	#define MyAppName "AutoYou"
#endif

#ifndef MyAppVersion
	#define MyAppVersion "81.2.0"
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

[Types]
Name: "easy"; Description: "Easy Setup (recommended)"
Name: "custom"; Description: "Custom setup"; Flags: iscustom

[Components]
Name: "core"; Description: "AutoYou desktop"; Types: easy custom; Flags: fixed
Name: "pi"; Description: "Pi coding subagent (downloads the optional Pi runtime)"; Types: easy
Name: "tv"; Description: "AutoYou TV & media streaming (configures FFmpeg for playback)"; Types: easy

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked

[Files]
; Include the entire published desktop distribution.
Source: "{#MyDistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "Setup-PiCoding.ps1"; DestDir: "{app}\Installer"; Components: pi; Flags: ignoreversion
Source: "Setup-FFmpeg.ps1"; DestDir: "{app}\Installer"; Components: tv; Flags: ignoreversion

[Icons]
Name: "{group}\AutoYou"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\AutoYou"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch AutoYou"; Flags: postinstall nowait skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  Params: string;
  ResultCode: Integer;
begin
  if (CurStep <> ssPostInstall) then
    exit;

  if WizardIsComponentSelected('pi') then begin
    WizardForm.StatusLabel.Caption := 'Setting up the optional Pi coding subagent...';
    Params := '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' +
      ExpandConstant('{app}\Installer\Setup-PiCoding.ps1') + '" -AppRoot "' +
      ExpandConstant('{app}') + '"';
    ResultCode := 1;
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Params, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Log('The optional Pi setup helper could not be started.');
    if ResultCode <> 0 then begin
      Log('Pi setup is pending. AutoYou can use the built-in coding backend.');
      if not WizardSilent then
        MsgBox('AutoYou is installed. Pi setup is pending; review pi-setup.json in the AutoYou user data folder. The built-in coding backend remains available.', mbInformation, MB_OK);
    end;
  end;

  if WizardIsComponentSelected('tv') then begin
    WizardForm.StatusLabel.Caption := 'Configuring FFmpeg for AutoYou TV media playback...';
    Params := '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' +
      ExpandConstant('{app}\Installer\Setup-FFmpeg.ps1') + '" -AppRoot "' +
      ExpandConstant('{app}') + '"';
    ResultCode := 1;
    if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      Params, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Log('The FFmpeg setup helper could not be started.');
    if ResultCode <> 0 then
      Log('FFmpeg setup could not complete automatically. tv_agent will fall back to local tools.');
  end;
end;
