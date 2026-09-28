# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

[CmdletBinding()]
param(
    [string]$DistRoot,
    [string]$OutputDir,
    [string]$ReleaseName = "AutoYou-win-x64",
    [string]$Version,
    [switch]$Clean,
    [switch]$IncludeInstaller,
    [string]$InnoSetupCompiler
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
if ([string]::IsNullOrWhiteSpace($DistRoot)) { $DistRoot = Join-Path $PSScriptRoot "dist\AutoYou-win-x64" }
if ([string]::IsNullOrWhiteSpace($OutputDir)) { $OutputDir = Join-Path $PSScriptRoot "dist\release" }

function Assert-PathExists {
    param(
        [string]$Path,
        [string]$Description
    )

    if (-not (Test-Path $Path)) {
        throw "Expected $Description at '$Path'."
    }
}

function Assert-LegalBundle {
    param(
        [string]$LegalRoot,
        [string]$Description
    )

    foreach ($fileName in @("LICENSE", "NOTICE.txt", "sbom.cdx.json", "THIRD-PARTY-NOTICES.md")) {
        Assert-PathExists -Path (Join-Path $LegalRoot $fileName) -Description "$Description $fileName"
    }
}

function Resolve-PythonExe {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }

    foreach ($commandName in @("python", "py")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }

    throw "Could not find python. Install Python or create .venv before release packaging."
}

function Invoke-StrictReleaseLegalGate {
    $gateScript = Join-Path $repoRoot "scripts\check_release_legal_gates.py"
    Assert-PathExists -Path $gateScript -Description "release legal gate script"
    $pythonExe = Resolve-PythonExe
    & $pythonExe $gateScript "--artifact-scope" "server" "--no-generate" "--strict-unknown-license"
    if ($LASTEXITCODE -ne 0) {
        throw "Release legal gate failed. Resolve open blockers before packaging official release artifacts."
    }
}

function Invoke-OfficialBuildAuthorizationGate {
    $gateScript = Join-Path $repoRoot "scripts\check_official_build_authorization.py"
    Assert-PathExists -Path $gateScript -Description "official build authorization script"
    $pythonExe = Resolve-PythonExe
    $artifactProfile = "autoyou-server-windows-default"
    $releaseProfilePath = Join-Path $distRootPath "release-profile.json"
    if (Test-Path $releaseProfilePath) {
        $profile = Get-Content -Raw -LiteralPath $releaseProfilePath | ConvertFrom-Json
        if ($profile.artifactProfile) {
            $artifactProfile = [string]$profile.artifactProfile
        }
    }
    & $pythonExe $gateScript "--required" "--artifact-profile" $artifactProfile
    if ($LASTEXITCODE -ne 0) {
        throw "Official build authorization failed. Complete the SignToROSS/OpenSign build-access agreement before packaging official release artifacts."
    }
}

function Resolve-BackendExecutablePath {
    param(
        [string]$BackendRoot
    )

    foreach ($candidateName in @("AutoYou.exe", "AutoYouServer.exe", "AutoYouDevServer.exe")) {
        $candidatePath = Join-Path $BackendRoot $candidateName
        if (Test-Path $candidatePath) {
            return $candidatePath
        }
    }

    return $null
}

function Get-AdkBrowserRoot {
    param(
        [string]$BackendRoot
    )

    $runtimeSitePackagesBrowserRoot = Join-Path $BackendRoot "runtime_site_packages\google\adk\cli\browser"
    if (Test-Path $runtimeSitePackagesBrowserRoot) {
        return $runtimeSitePackagesBrowserRoot
    }

    return (Join-Path $BackendRoot "google\adk\cli\browser")
}

function Assert-ReleasePayload {
    param(
        [string]$BundleRoot
    )

    $backendRoot = Join-Path $BundleRoot "Backend"
    $adkBrowserRoot = Get-AdkBrowserRoot -BackendRoot $backendRoot

    $requiredPaths = @(
        @{ Path = (Join-Path $BundleRoot "AutoYou.exe"); Description = "tray host executable" },
        @{ Path = (Join-Path $BundleRoot "AutoYou.dll"); Description = "tray host assembly" },
        @{ Path = (Join-Path $BundleRoot "AutoYou.deps.json"); Description = "dependency manifest" },
        @{ Path = (Join-Path $BundleRoot "AutoYou.runtimeconfig.json"); Description = "runtime config" },
        @{ Path = (Join-Path $BundleRoot "release-profile.json"); Description = "release profile metadata" },
        @{ Path = (Join-Path $BundleRoot "Assets\logo.ico"); Description = "tray host icon asset" },
        @{ Path = (Join-Path $backendRoot "runtime\node\node.exe"); Description = "portable Node.js runtime" },
        @{ Path = (Join-Path $backendRoot "runtime\tunnelmole\tmole.exe"); Description = "Tunnelmole binary runtime" },
        @{ Path = (Join-Path $backendRoot "runtime\playwright"); Description = "Playwright runtime folder" },
        @{ Path = (Join-Path $backendRoot "node\whatsapp"); Description = "WhatsApp bridge assets" },
        @{ Path = (Join-Path $backendRoot "release-profile.json"); Description = "backend release profile metadata" },
        @{ Path = (Join-Path $adkBrowserRoot "index.html"); Description = "ADK browser index" },
        @{ Path = (Join-Path $adkBrowserRoot "assets\audio-processor.js"); Description = "ADK audio processor" },
        @{ Path = (Join-Path $adkBrowserRoot "assets\config\runtime-config.json"); Description = "ADK runtime config" }
    )

    foreach ($requiredPath in $requiredPaths) {
        Assert-PathExists -Path $requiredPath.Path -Description $requiredPath.Description
    }

    $sourceIcon = Join-Path $repoRoot "assets\logo.ico"
    $bundleIcon = Join-Path $BundleRoot "Assets\logo.ico"
    if ((Get-FileHash -Algorithm SHA256 $sourceIcon).Hash -ne (Get-FileHash -Algorithm SHA256 $bundleIcon).Hash) {
        throw "The packaged tray host icon '$bundleIcon' does not match '$sourceIcon'."
    }

    $hostExecutable = Join-Path $BundleRoot "AutoYou.exe"
    $backendExecutable = Resolve-BackendExecutablePath -BackendRoot $backendRoot
    if ((Get-FileHash -Algorithm SHA256 $hostExecutable).Hash -eq (Get-FileHash -Algorithm SHA256 $backendExecutable).Hash) {
        throw "The release bundle's root AutoYou.exe is the backend executable, not the native tray host."
    }

    Assert-LegalBundle -LegalRoot (Join-Path $BundleRoot "Legal") -Description "desktop legal bundle"
    Assert-LegalBundle -LegalRoot (Join-Path $backendRoot "Legal") -Description "backend legal bundle"

    $backendExecutablePath = Resolve-BackendExecutablePath -BackendRoot (Join-Path $BundleRoot "Backend")
    if (-not $backendExecutablePath) {
        throw "Expected packaged backend executable in '$BundleRoot\Backend' (AutoYou.exe or legacy backend names)."
    }
}

function Get-SafeVersionSuffix {
    param(
        [string]$Value
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $null
    }

    $sanitized = ($Value -replace "[^A-Za-z0-9._-]", "-").Trim("-")
    if ([string]::IsNullOrWhiteSpace($sanitized)) {
        throw "Provided -Version '$Value' cannot be converted to a safe file name component."
    }

    return $sanitized
}

function New-ZipFromDirectory {
    param(
        [string]$SourceDir,
        [string]$DestinationZip
    )

    Add-Type -AssemblyName System.IO.Compression.FileSystem

    if (Test-Path $DestinationZip) {
        Remove-Item $DestinationZip -Force
    }

    $destinationParent = Split-Path -Parent $DestinationZip
    if (-not [string]::IsNullOrWhiteSpace($destinationParent)) {
        New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
    }

    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $SourceDir,
        $DestinationZip,
        [System.IO.Compression.CompressionLevel]::Optimal,
        $true
    )
}

function Resolve-InnoSetupCompilerPath {
    param(
        [string]$RequestedPath
    )

    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        Assert-PathExists -Path $RequestedPath -Description "Inno Setup compiler"
        return [System.IO.Path]::GetFullPath($RequestedPath)
    }

    foreach ($commandName in @("ISCC.exe", "ISCC")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }

    foreach ($candidatePath in @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe",
        (Join-Path $env:LOCALAPPDATA "Programs\Antigravity IDE\resources\app\node_modules\innosetup\bin\ISCC.exe")
    )) {
        if (Test-Path $candidatePath) {
            return $candidatePath
        }
    }

    throw "Inno Setup compiler not found. Install Inno Setup 6 or pass -InnoSetupCompiler <path>."
}

function Get-FreeSubstDriveLetter {
    $usedDriveLetters = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)

    foreach ($drive in (Get-PSDrive -PSProvider FileSystem)) {
        $null = $usedDriveLetters.Add($drive.Name)
    }

    foreach ($letter in @("P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z")) {
        if (-not $usedDriveLetters.Contains($letter)) {
            return $letter
        }
    }

    throw "No free drive letter is available for a temporary subst mapping."
}

function New-TemporarySubstDrive {
    param(
        [string]$TargetPath
    )

    $driveLetter = Get-FreeSubstDriveLetter
    $driveRoot = "${driveLetter}:"

    & subst $driveRoot $TargetPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$driveRoot\")) {
        throw "Failed to create temporary subst drive '$driveRoot' for '$TargetPath'."
    }

    return $driveRoot
}

function Remove-TemporarySubstDrive {
    param(
        [string]$DriveRoot
    )

    if ([string]::IsNullOrWhiteSpace($DriveRoot)) {
        return
    }

    & subst $DriveRoot /D | Out-Null
}

function Build-Installer {
    param(
        [string]$InstallerScript,
        [string]$DistRoot,
        [string]$OutputDir,
        [string]$Version,
        [string]$OutputBaseFilename,
        [string]$RequestedCompiler
    )

    $compilerPath = Resolve-InnoSetupCompilerPath -RequestedPath $RequestedCompiler
    $substDriveRoot = $null

    try {
        # Some packaged assets exceed MAX_PATH from the repo root; compile through
        # a temporary subst drive so Inno Setup sees a much shorter source path.
        $substDriveRoot = New-TemporarySubstDrive -TargetPath $DistRoot

    $arguments = @(
        "/Qp",
        "/DMyDistDir=$substDriveRoot",
        "/DMyOutputDir=$OutputDir",
        "/DMyOutputBaseFilename=$OutputBaseFilename"
    )

    if (-not [string]::IsNullOrWhiteSpace($Version)) {
        $arguments += "/DMyAppVersion=$Version"
    }

    $arguments += $InstallerScript

    & $compilerPath @arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "Installer build failed."
    }

    $installerPath = Join-Path $OutputDir "$OutputBaseFilename.exe"
    Assert-PathExists -Path $installerPath -Description "compiled installer executable"
    return $installerPath
    }
    finally {
        Remove-TemporarySubstDrive -DriveRoot $substDriveRoot
    }
}

function Write-Sha256Manifest {
    param(
        [string[]]$ArtifactPaths,
        [string]$OutputPath
    )

    $lines = foreach ($artifactPath in ($ArtifactPaths | Sort-Object)) {
        $hash = (Get-FileHash -Path $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash *$(Split-Path -Leaf $artifactPath)"
    }

    [System.IO.File]::WriteAllLines($OutputPath, $lines)
}

$distRootPath = [System.IO.Path]::GetFullPath($DistRoot)
$outputDirPath = [System.IO.Path]::GetFullPath($OutputDir)
$distRootWithSeparator = $distRootPath.TrimEnd("\") + "\"
$outputDirWithSeparator = $outputDirPath.TrimEnd("\") + "\"

if ($outputDirWithSeparator.StartsWith($distRootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "-OutputDir must not be inside -DistRoot because the release archive would include its own generated artifacts."
}

Invoke-OfficialBuildAuthorizationGate
Invoke-StrictReleaseLegalGate
Assert-PathExists -Path $distRootPath -Description "published desktop bundle root"
Assert-ReleasePayload -BundleRoot $distRootPath

if ($Clean -and (Test-Path $outputDirPath)) {
    Remove-Item $outputDirPath -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $outputDirPath | Out-Null

$versionSuffix = Get-SafeVersionSuffix -Value $Version
$zipBaseName = if ($versionSuffix) { "$ReleaseName-v$versionSuffix" } else { $ReleaseName }
$zipPath = Join-Path $outputDirPath "$zipBaseName.zip"

Write-Host "Creating portable release archive: $zipPath"
New-ZipFromDirectory -SourceDir $distRootPath -DestinationZip $zipPath

$artifacts = [System.Collections.Generic.List[string]]::new()
$artifacts.Add($zipPath)

if ($IncludeInstaller) {
    $installerScriptPath = Join-Path $repoRoot "installer\AutoYouInstaller.iss"
    $installerBaseName = if ($versionSuffix) { "AutoYouSetup-win-x64-v$versionSuffix" } else { "AutoYouSetup-win-x64" }

    Assert-PathExists -Path $installerScriptPath -Description "Inno Setup installer script"
    Write-Host "Building installer: $installerBaseName.exe"

    $installerBuildParameters = @{
        InstallerScript = $installerScriptPath
        DistRoot = $distRootPath
        OutputDir = $outputDirPath
        Version = $Version
        OutputBaseFilename = $installerBaseName
        RequestedCompiler = $InnoSetupCompiler
    }

    $installerPath = Build-Installer @installerBuildParameters

    $artifacts.Add($installerPath)
}

$checksumPath = Join-Path $outputDirPath "SHA256SUMS.txt"
Write-Host "Writing checksum manifest: $checksumPath"
Write-Sha256Manifest -ArtifactPaths $artifacts.ToArray() -OutputPath $checksumPath

Write-Host "Release artifacts ready:"
foreach ($artifactPath in $artifacts) {
    Write-Host " - $artifactPath"
}
Write-Host " - $checksumPath"

[pscustomobject]@{
    DistRoot = $distRootPath
    OutputDir = $outputDirPath
    ArtifactPaths = $artifacts.ToArray()
    ChecksumManifest = $checksumPath
}
