# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

[CmdletBinding()]
param(
    [string]$DistRoot,
    [string]$OutputDir,
    [string]$Version,
    [string]$IdentityName = $(if ($env:AUTOYOU_STORE_SERVER_IDENTITY_NAME) { $env:AUTOYOU_STORE_SERVER_IDENTITY_NAME } elseif ($env:AUTOYOU_STORE_IDENTITY_NAME) { $env:AUTOYOU_STORE_IDENTITY_NAME } else { "OpenStorey.AutoYou" }),
    [string]$Publisher = $(if ($env:AUTOYOU_STORE_SERVER_PUBLISHER) { $env:AUTOYOU_STORE_SERVER_PUBLISHER } elseif ($env:AUTOYOU_STORE_PUBLISHER) { $env:AUTOYOU_STORE_PUBLISHER } else { "CN=4733292C-6DCF-469B-883A-A348B372C25B" }),
    [string]$PublisherDisplayName = "OpenStorey",
    [string]$DisplayName = "AutoYou",
    [string]$PackageFileName,
    [string]$MakeAppxPath,
    [switch]$SkipReleaseLegalGate,
    [switch]$KeepStaging
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
if ([string]::IsNullOrWhiteSpace($DistRoot)) { $DistRoot = Join-Path $PSScriptRoot "dist\AutoYou-win-x64" }
if ([string]::IsNullOrWhiteSpace($OutputDir)) { $OutputDir = Join-Path $PSScriptRoot "dist\msix" }

function Assert-PathExists {
    param([string]$Path, [string]$Description)
    if (-not (Test-Path -LiteralPath $Path)) { throw "Expected $Description at '$Path'." }
}

function Assert-LegalBundle {
    param([string]$LegalRoot, [string]$Description)
    foreach ($fileName in @("LICENSE", "NOTICE.txt", "sbom.cdx.json", "THIRD-PARTY-NOTICES.md")) {
        Assert-PathExists -Path (Join-Path $LegalRoot $fileName) -Description "$Description $fileName"
    }
}

function Resolve-PythonExe {
    foreach ($candidate in @((Join-Path $repoRoot ".venv\Scripts\python.exe"), (Join-Path $PSScriptRoot "venv\Scripts\python.exe"))) {
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    foreach ($commandName in @("python", "py")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) { return $command.Source }
    }
    throw "Could not find python."
}

function Invoke-StrictReleaseLegalGate {
    if ($SkipReleaseLegalGate) {
        Write-Warning "Skipping strict release legal gate for MSIX packaging."
        return
    }
    $gateScript = Join-Path $repoRoot "scripts\check_release_legal_gates.py"
    Assert-PathExists -Path $gateScript -Description "release legal gate script"
    & (Resolve-PythonExe) $gateScript --artifact-scope server --no-generate --strict-unknown-license
    if ($LASTEXITCODE -ne 0) { throw "Release legal gate failed. Resolve open blockers before official Store submission." }
}

function Invoke-OfficialBuildAuthorizationGate {
    $gateScript = Join-Path $repoRoot "scripts\check_official_build_authorization.py"
    Assert-PathExists -Path $gateScript -Description "official build authorization script"
    $artifactProfile = "autoyou-server-windows-default"
    $releaseProfilePath = Join-Path $distRootPath "release-profile.json"
    if (Test-Path $releaseProfilePath) {
        $profile = Get-Content -Raw -LiteralPath $releaseProfilePath | ConvertFrom-Json
        if ($profile.artifactProfile) {
            $artifactProfile = [string]$profile.artifactProfile
        }
    }
    & (Resolve-PythonExe) $gateScript "--required" "--artifact-profile" $artifactProfile
    if ($LASTEXITCODE -ne 0) { throw "Official build authorization failed. Complete the SignToROSS/OpenSign build-access agreement before official Store submission." }
}

function Resolve-MakeAppx {
    param([string]$RequestedPath)
    if (-not [string]::IsNullOrWhiteSpace($RequestedPath)) {
        Assert-PathExists -Path $RequestedPath -Description "makeappx.exe"
        return [System.IO.Path]::GetFullPath($RequestedPath)
    }
    $cmd = Get-Command makeappx.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($root in @("${env:ProgramFiles(x86)}\Windows Kits\10\bin", "${env:ProgramFiles}\Windows Kits\10\bin")) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
        $found = Get-ChildItem -Path $root -Recurse -Filter makeappx.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\x64\\" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($found) { return $found.FullName }
    }

    # The validated Windows AMD profile may use the SDK Build Tools package
    # restored into NuGet instead of a machine-wide Windows SDK installation.
    $nugetRoot = if ($env:NUGET_PACKAGES) {
        $env:NUGET_PACKAGES
    } elseif ($env:USERPROFILE) {
        Join-Path $env:USERPROFILE ".nuget\packages"
    }
    if ($nugetRoot -and (Test-Path -LiteralPath $nugetRoot)) {
        $found = Get-ChildItem -Path (Join-Path $nugetRoot "microsoft.windows.sdk.buildtools") -Recurse -Filter makeappx.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\x64\\" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    throw "makeappx.exe not found. Install the Windows SDK."
}

function Get-MsixVersion {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        $versionFile = Join-Path $repoRoot "VERSION"
        Assert-PathExists -Path $versionFile -Description "VERSION file"
        $Value = (Get-Content -Raw $versionFile).Trim()
    }
    $parts = @($Value.Split(".") | ForEach-Object { [int]$_ })
    if ($parts.Count -lt 3 -or $parts.Count -gt 4) { throw "MSIX version must have 3 or 4 numeric parts: $Value" }
    while ($parts.Count -lt 4) { $parts += 0 }
    $parts[3] = 0
    return ($parts -join ".")
}

function Resolve-BackendExecutablePath {
    param([string]$BackendRoot)
    foreach ($candidateName in @("AutoYou.exe", "AutoYouServer.exe", "AutoYouDevServer.exe")) {
        $candidatePath = Join-Path $BackendRoot $candidateName
        if (Test-Path -LiteralPath $candidatePath) { return $candidatePath }
    }
    return $null
}

function Get-AdkBrowserRoot {
    param([string]$BackendRoot)
    $runtimeSitePackagesBrowserRoot = Join-Path $BackendRoot "runtime_site_packages\google\adk\cli\browser"
    if (Test-Path -LiteralPath $runtimeSitePackagesBrowserRoot) { return $runtimeSitePackagesBrowserRoot }
    return (Join-Path $BackendRoot "google\adk\cli\browser")
}

function Assert-ServerPayload {
    param([string]$BundleRoot)
    $backendRoot = Join-Path $BundleRoot "Backend"
    $adkBrowserRoot = Get-AdkBrowserRoot -BackendRoot $backendRoot
    foreach ($requiredPath in @(
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
    )) {
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
        throw "The Store bundle's root AutoYou.exe is the backend executable, not the native tray host."
    }
    Assert-LegalBundle -LegalRoot (Join-Path $BundleRoot "Legal") -Description "desktop legal bundle"
    Assert-LegalBundle -LegalRoot (Join-Path $backendRoot "Legal") -Description "backend legal bundle"
    if (-not (Resolve-BackendExecutablePath -BackendRoot $backendRoot)) {
        throw "Expected packaged backend executable in '$backendRoot'."
    }
}

function Assert-StoreDesktopManifest {
    param([string]$ManifestPath)
    $manifestXml = Get-Content -LiteralPath $ManifestPath -Raw
    foreach ($required in @(
        @{ Pattern = 'RuntimeBehavior\s*=\s*"packagedClassicApp"'; Description = "packagedClassicApp runtime behavior" },
        @{ Pattern = 'TrustLevel\s*=\s*"mediumIL"'; Description = "mediumIL trust level" },
        @{ Pattern = '<\s*rescap:Capability\s+Name\s*=\s*"runFullTrust"\s*/?\s*>'; Description = "runFullTrust restricted capability" }
    )) {
        if ($manifestXml -notmatch $required.Pattern) {
            throw "Store MSIX manifest is missing $($required.Description)."
        }
    }
    foreach ($blocked in @(
        @{ Pattern = "allowElevation"; Description = "elevation capability" },
        @{ Pattern = "windows\.service"; Description = "Windows service extension" },
        @{ Pattern = "<\s*(?:[A-Za-z0-9._-]+:)?Service\b"; Description = "service declaration" },
        @{ Pattern = "requireAdministrator"; Description = "administrator execution level" }
    )) {
        if ($manifestXml -match $blocked.Pattern) {
            throw "Store MSIX manifest must not include $($blocked.Description)."
        }
    }
}

function Copy-IfPresent {
    param([string]$Source, [string]$Destination)
    if (Test-Path -LiteralPath $Source) { Copy-Item -LiteralPath $Source -Destination $Destination -Force }
}

function Remove-MutableRuntimeState {
    param([string]$Root)
    foreach ($fileName in @(
        "config.keystore.enc",
        "config.encrypted",
        "config.encrypted.bak",
        "agent_install_registry.json",
        "agent_frontends_registry.json",
        "login_ui_state.db",
        "page_feed.db",
        "sessions.db",
        "sessions.db.bak"
    )) {
        Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $fileName -Force -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

function Format-CommandArgument {
    param([string]$Value)
    return '"' + ($Value -replace '"', '\"') + '"'
}

if ([string]::IsNullOrWhiteSpace($IdentityName) -or [string]::IsNullOrWhiteSpace($Publisher)) {
    throw "Provide Partner Center MSIX identity values: -IdentityName <Package/Identity Name> -Publisher <Package/Identity Publisher>. Find them in the app's Product management > App identity."
}

$distRootPath = [System.IO.Path]::GetFullPath($DistRoot)
$outputDirPath = [System.IO.Path]::GetFullPath($OutputDir)
$stagingDir = Join-Path $outputDirPath "staging"
$assetsDir = Join-Path $stagingDir "Assets"
$msixVersion = Get-MsixVersion -Value $Version
if ([string]::IsNullOrWhiteSpace($PackageFileName)) {
    $safeIdentity = $IdentityName -replace "[^A-Za-z0-9._-]", "-"
    $PackageFileName = "$safeIdentity-$msixVersion-x64.msix"
}
$packagePath = Join-Path $outputDirPath $PackageFileName

$distRootWithSeparator = $distRootPath.TrimEnd("\") + "\"
$outputDirWithSeparator = $outputDirPath.TrimEnd("\") + "\"
if ($outputDirWithSeparator.StartsWith($distRootWithSeparator, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "-OutputDir must not be inside -DistRoot because the MSIX would include its own generated artifacts."
}

Invoke-OfficialBuildAuthorizationGate
Invoke-StrictReleaseLegalGate
Assert-PathExists -Path $distRootPath -Description "published AutoYou server bundle root"
Assert-ServerPayload -BundleRoot $distRootPath

Remove-Item -LiteralPath $stagingDir -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $stagingDir, $assetsDir, $outputDirPath | Out-Null
$pythonExe = Resolve-PythonExe
$fastCopyScript = Join-Path $repoRoot "scripts\copy_payload_fast.py"
& $pythonExe $fastCopyScript $distRootPath $stagingDir
Remove-MutableRuntimeState -Root $stagingDir

$sourceAssets = Join-Path $repoRoot "servers\windows\AutoYouWindowsHost\Assets"
Assert-PathExists -Path $sourceAssets -Description "Windows app assets"
Copy-IfPresent (Join-Path $sourceAssets "StoreLogo.png") (Join-Path $assetsDir "StoreLogo.png")
Copy-IfPresent (Join-Path $sourceAssets "Square44x44Logo.scale-200.png") (Join-Path $assetsDir "Square44x44Logo.scale-200.png")
Copy-IfPresent (Join-Path $sourceAssets "Square44x44Logo.scale-200.png") (Join-Path $assetsDir "Square44x44Logo.png")
Copy-IfPresent (Join-Path $sourceAssets "Square150x150Logo.scale-200.png") (Join-Path $assetsDir "Square150x150Logo.scale-200.png")
Copy-IfPresent (Join-Path $sourceAssets "Square150x150Logo.scale-200.png") (Join-Path $assetsDir "Square150x150Logo.png")
Copy-IfPresent (Join-Path $sourceAssets "Wide310x150Logo.scale-200.png") (Join-Path $assetsDir "Wide310x150Logo.scale-200.png")
Copy-IfPresent (Join-Path $sourceAssets "Wide310x150Logo.scale-200.png") (Join-Path $assetsDir "Wide310x150Logo.png")
Copy-IfPresent (Join-Path $sourceAssets "SplashScreen.scale-200.png") (Join-Path $assetsDir "SplashScreen.scale-200.png")
Copy-IfPresent (Join-Path $sourceAssets "SplashScreen.scale-200.png") (Join-Path $assetsDir "SplashScreen.png")

$manifestPath = Join-Path $stagingDir "AppxManifest.xml"
@"
<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap uap10 rescap">
  <Identity Name="$IdentityName" Publisher="$Publisher" Version="$msixVersion" ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>$DisplayName</DisplayName>
    <PublisherDisplayName>$PublisherDisplayName</PublisherDisplayName>
    <Logo>Assets\StoreLogo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.19041.0" MaxVersionTested="10.0.26100.0" />
  </Dependencies>
  <Resources>
    <Resource Language="en-US" />
  </Resources>
  <Applications>
    <Application Id="AutoYou" Executable="AutoYou.exe" uap10:RuntimeBehavior="packagedClassicApp" uap10:TrustLevel="mediumIL">
      <uap:VisualElements
        DisplayName="$DisplayName"
        Description="AutoYou desktop server host"
        BackgroundColor="transparent"
        Square150x150Logo="Assets\Square150x150Logo.png"
        Square44x44Logo="Assets\Square44x44Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\Wide310x150Logo.png" />
        <uap:SplashScreen Image="Assets\SplashScreen.png" />
      </uap:VisualElements>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
    <Capability Name="internetClient" />
    <Capability Name="privateNetworkClientServer" />
    <DeviceCapability Name="bluetooth" />
  </Capabilities>
</Package>
"@ | Set-Content -LiteralPath $manifestPath -Encoding UTF8
Assert-StoreDesktopManifest -ManifestPath $manifestPath

$makeAppx = Resolve-MakeAppx -RequestedPath $MakeAppxPath
if (Test-Path -LiteralPath $packagePath) { Remove-Item -LiteralPath $packagePath -Force }
$makeAppxOutput = & $makeAppx pack /d $stagingDir /p $packagePath /o 2>&1
if ($LASTEXITCODE -ne 0) {
    $makeAppxOutput | Select-Object -Last 80 | Out-Host
    throw "MSIX package build failed."
}
$makeAppxOutput | Where-Object { $_ -match "Package creation succeeded|Package creation failed|error:" } | Out-Host

$checksumPath = Join-Path $outputDirPath "SHA256SUMS-msix.txt"
$hash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText($checksumPath, "$hash *$(Split-Path -Leaf $packagePath)`r`n")
if (-not $KeepStaging) { Remove-Item -LiteralPath $stagingDir -Recurse -Force -ErrorAction SilentlyContinue }
$verifyCommand = "python scripts\verify_windows_store_msix_artifacts.py --server-only --server-msix $(Format-CommandArgument $packagePath) --server-sha256 $hash --server-identity-name $(Format-CommandArgument $IdentityName) --server-publisher $(Format-CommandArgument $Publisher)"

[pscustomobject]@{
    PackagePath = $packagePath
    ChecksumManifest = $checksumPath
    IdentityName = $IdentityName
    Publisher = $Publisher
    Version = $msixVersion
    Signed = $false
    StoreSigning = "Microsoft Store re-signs MSIX after certification"
    StagingKept = [bool]$KeepStaging
    VerifyCommand = $verifyCommand
}
