# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

[CmdletBinding()]
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [switch]$Clean,
    [switch]$SkipBackend,
    [ValidateSet("clang", "msvc", "zig")]
    [string]$BackendCompiler = "clang",
    [switch]$IncludeCognee,
    [switch]$AllowOpenReleaseBlockers,
    [switch]$SkipReleaseLegalGate,
    [ValidateSet("binary-default", "connector-full", "training-full")]
    [string]$ReleaseProfile = "binary-default",
    [string]$Version,
    [string]$OutputRoot,
    [int]$JobCount = 0
)

$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path
$hostProject = Join-Path $scriptRoot "AutoYouWindowsHost\AutoYouWindowsHost.csproj"
$publishRoot = if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    Join-Path $scriptRoot "dist\AutoYou-win-x64"
} else {
    [System.IO.Path]::GetFullPath($OutputRoot)
}
$backendRoot = Join-Path $scriptRoot "artifacts\backend\AutoYouServer"
$defaultPythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
$hostPublishSucceeded = $false
$hostExecutableHash = $null

function Assert-SafePublishRoot {
    param(
        [string]$Path,
        [string]$RepoRoot
    )

    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $resolvedRepo = [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $pathRoot = [System.IO.Path]::GetPathRoot($resolved).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
    $comparison = [System.StringComparison]::OrdinalIgnoreCase
    if ([string]::IsNullOrWhiteSpace($resolved) -or $resolved.Equals($pathRoot, $comparison)) {
        throw "Refusing to use a filesystem root as the Windows publish output: '$Path'."
    }
    if ($resolvedRepo.Equals($resolved, $comparison) -or $resolvedRepo.StartsWith($resolved + [System.IO.Path]::DirectorySeparatorChar, $comparison)) {
        throw "Refusing to use the repository or one of its parents as the Windows publish output: '$Path'."
    }
    if ((Split-Path -Leaf $resolved) -ne "AutoYou-win-x64") {
        throw "Windows publish output must end in 'AutoYou-win-x64': '$Path'."
    }
    return $resolved
}

$publishRoot = Assert-SafePublishRoot -Path $publishRoot -RepoRoot $repoRoot

function Resolve-ReleaseProfileSettings {
    param(
        [string]$Profile
    )

    switch ($Profile) {
        "training-full" {
            return [pscustomobject]@{
                ReleaseProfile = "training-full"
                Requirements = "training-full"
                RequirementsFile = (Join-Path $repoRoot "requirements\training-full.txt")
                ArtifactId = "autoyou-server-windows-connector-full"
            }
        }
        "connector-full" {
            return [pscustomobject]@{
                ReleaseProfile = "connector-full"
                Requirements = "full"
                RequirementsFile = (Join-Path $repoRoot "requirements.txt")
                ArtifactId = "autoyou-server-windows-connector-full"
            }
        }
        default {
            return [pscustomobject]@{
                ReleaseProfile = "binary-default"
                Requirements = "binary-default"
                RequirementsFile = (Join-Path $repoRoot "requirements\binary-default.txt")
                ArtifactId = "autoyou-server-windows-default"
            }
        }
    }
}

$releaseSettings = Resolve-ReleaseProfileSettings -Profile $ReleaseProfile

function Resolve-AppVersion {
    param(
        [string]$RequestedVersion
    )

    $candidate = $RequestedVersion
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        $versionFile = Join-Path $repoRoot "VERSION"
        if (Test-Path -LiteralPath $versionFile) {
            $candidate = (Get-Content -Raw -LiteralPath $versionFile).Trim()
        }
    }
    if ([string]::IsNullOrWhiteSpace($candidate)) {
        throw "Could not resolve the Windows app version from -Version or the repository VERSION file."
    }
    if ($candidate -notmatch "^\d+\.\d+\.\d+(?:\.\d+)?$") {
        throw "Windows app version must have 3 or 4 numeric parts: $candidate"
    }
    return $candidate
}

$appVersion = Resolve-AppVersion -RequestedVersion $Version

function Get-BackendRuntimeSpec {
    param(
        [string]$PythonExe,
        [string]$RepoRoot,
        [string]$RequirementsFile
    )

    $scriptPath = Join-Path $RepoRoot "scripts\read_backend_runtime_pins.py"
    if ((Test-Path $PythonExe) -and (Test-Path $scriptPath) -and (Test-Path $RequirementsFile)) {
        $arguments = @(
            $scriptPath,
            "--requirements-file", $RequirementsFile,
            "--format", "spec",
            "--packages",
            "google-adk",
            "google-genai",
            "google-cloud-aiplatform",
            "fastapi"
        )
        $output = & $PythonExe @arguments 2>&1
        if ($LASTEXITCODE -eq 0) {
            return (($output -join "`n").Trim())
        }
    }

    return "google-adk/current, google-genai/current, google-cloud-aiplatform/current, fastapi/current"
}

$backendRuntimeSpec = Get-BackendRuntimeSpec -PythonExe $defaultPythonExe -RepoRoot $repoRoot -RequirementsFile $releaseSettings.RequirementsFile

function Get-GitCommit {
    param(
        [string]$RepoRoot
    )

    try {
        $commitOutput = @(& git -C $RepoRoot rev-parse HEAD 2>$null)
        $gitExitCode = $LASTEXITCODE
        $commit = ($commitOutput | Select-Object -First 1)
        if ($gitExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($commit)) {
            return $commit.Trim()
        }
    }
    catch {
    }
    return "unknown"
}

function Write-ReleaseProfileMetadata {
    param(
        [string]$TargetPath,
        [object]$Settings,
        [string]$Version
    )

    $metadata = [ordered]@{
        releaseProfile = $Settings.ReleaseProfile
        dependencyProfile = $Settings.Requirements
        artifactProfile = $Settings.ArtifactId
        builtAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        commit = (Get-GitCommit -RepoRoot $repoRoot)
        version = $Version
        legalBundle = "Legal"
    }

    $json = $metadata | ConvertTo-Json -Depth 4
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($TargetPath, $json + [Environment]::NewLine, $utf8NoBom)
}

function Get-AdkBrowserRoot {
    param(
        [string]$BundleRoot
    )

    $runtimeSitePackagesBrowserRoot = Join-Path $BundleRoot "runtime_site_packages\google\adk\cli\browser"
    if (Test-Path $runtimeSitePackagesBrowserRoot) {
        return $runtimeSitePackagesBrowserRoot
    }

    return (Join-Path $BundleRoot "google\adk\cli\browser")
}

function Assert-AdkBrowserAssetsPresent {
    param(
        [string]$BundleRoot
    )

    $browserRoot = Get-AdkBrowserRoot -BundleRoot $BundleRoot
    $requiredFiles = @(
        (Join-Path $browserRoot "index.html"),
        (Join-Path $browserRoot "assets\audio-processor.js"),
        (Join-Path $browserRoot "assets\config\runtime-config.json")
    )

    foreach ($filePath in $requiredFiles) {
        if (-not (Test-Path $filePath)) {
            throw "Expected Google ADK browser asset '$filePath' was not found in the desktop publish."
        }
    }
}

function Assert-HostPublishPresent {
    param(
        [string]$PublishRoot
    )

    $requiredFiles = @(
        (Join-Path $PublishRoot "AutoYou.exe"),
        (Join-Path $PublishRoot "Assets\logo.ico")
    )

    foreach ($filePath in $requiredFiles) {
        if (-not (Test-Path $filePath)) {
            throw "Expected desktop host asset '$filePath' was not found in the desktop publish."
        }
    }

    $sourceIcon = Join-Path $repoRoot "assets\logo.ico"
    $publishedIcon = Join-Path $PublishRoot "Assets\logo.ico"
    if ((Get-FileHash -Algorithm SHA256 $sourceIcon).Hash -ne (Get-FileHash -Algorithm SHA256 $publishedIcon).Hash) {
        throw "Published desktop host icon '$publishedIcon' does not match '$sourceIcon'."
    }
}

function Assert-NativeRemoteDesktopBackendCurrent {
    param(
        [string]$BundleRoot
    )

    foreach ($modulePattern in @(
        "runtime_modules\shared\remote_desktop_input*.pyd",
        "runtime_modules\shared\remote_desktop_settings*.pyd"
    )) {
        $resolvedPattern = Join-Path $BundleRoot $modulePattern
        if (-not (Get-ChildItem -Path $resolvedPattern -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            throw "Backend bundle is stale: expected native remote-desktop module matching '$resolvedPattern'."
        }
    }

    foreach ($packageName in @("mss", "pyautogui")) {
        $packagePath = Join-Path $BundleRoot "runtime_site_packages\$packageName"
        if (-not (Test-Path $packagePath -PathType Container)) {
            throw "Backend bundle is stale: native remote-desktop dependency is missing at '$packagePath'."
        }
    }

    foreach ($assetName in @("admin-ui.js", "admin-ui.css")) {
        $sourceAsset = Join-Path $repoRoot "assets\$assetName"
        $packagedAsset = Join-Path $BundleRoot "assets\$assetName"
        if ((-not (Test-Path $packagedAsset)) -or
            ((Get-FileHash -Algorithm SHA256 $sourceAsset).Hash -ne (Get-FileHash -Algorithm SHA256 $packagedAsset).Hash)) {
            throw "Backend bundle is stale: packaged admin asset '$packagedAsset' does not match source."
        }
    }
}

function Assert-EmotiVoiceRuntimePresent {
    param(
        [string]$BundleRoot
    )

    if ($releaseSettings.Requirements -notin @("full", "source-full", "connector-full", "training-full")) {
        return
    }

    $vendorRoot = Join-Path $BundleRoot "runtime_modules\vendor\emotivoice"
    foreach ($module in @(
        "frontend",
        "frontend_cn",
        "frontend_en",
        "models\prompt_tts_modified\jets",
        "models\prompt_tts_modified\model_open_source",
        "models\prompt_tts_modified\simbert",
        "models\prompt_tts_modified\modules\alignment",
        "models\prompt_tts_modified\modules\encoder",
        "models\prompt_tts_modified\modules\initialize",
        "models\prompt_tts_modified\modules\variance",
        "models\hifigan\models",
        "models\hifigan\get_random_segments",
        "config\joint\config"
    )) {
        $modulePattern = Join-Path $vendorRoot ($module + "*.pyd")
        if (-not (Get-ChildItem -Path $modulePattern -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            throw "Windows release is missing compiled EmotiVoice module '$modulePattern'."
        }
    }

    foreach ($relativeAsset in @(
        "config\joint\config.yaml",
        "data\youdao\text\emotion",
        "data\youdao\text\energy",
        "data\youdao\text\pitch",
        "data\youdao\text\speaker2",
        "data\youdao\text\speed",
        "data\youdao\text\tokenlist",
        "lexicon\librispeech-lexicon.txt",
        "LICENSE"
    )) {
        $assetPath = Join-Path $vendorRoot $relativeAsset
        if (-not (Test-Path -LiteralPath $assetPath -PathType Leaf)) {
            throw "Windows release is missing EmotiVoice runtime asset '$assetPath'."
        }
    }
}

function Copy-ReleaseLegalBundle {
    param(
        [string]$ArtifactId,
        [string]$TargetPath,
        [switch]$Generate
    )

    $legalCopyScript = Join-Path $repoRoot "scripts\copy_release_legal_artifacts.py"
    if (-not (Test-Path $legalCopyScript)) {
        throw "Expected release legal copy script at '$legalCopyScript'."
    }

    $pythonExe = "python"
    if (Test-Path $defaultPythonExe) {
        $pythonExe = $defaultPythonExe
    }

    $arguments = @(
        $legalCopyScript,
        "--artifact", $ArtifactId,
        "--target", $TargetPath
    )
    if ($Generate) {
        $arguments += "--generate"
    }

    Write-Host "Copying $ArtifactId legal bundle to $TargetPath..."
    & $pythonExe @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to copy release legal bundle for '$ArtifactId'."
    }
}

function Resolve-PythonExe {
    if (Test-Path $defaultPythonExe) {
        return $defaultPythonExe
    }

    foreach ($commandName in @("python", "py")) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }

    throw "Could not find python. Install Python or create .venv before release publishing."
}

function Invoke-StrictReleaseLegalGate {
    if ($Configuration -ne "Release") {
        return
    }
    if ($SkipReleaseLegalGate) {
        Write-Warning "Skipping strict release legal gate for functional build."
        return
    }

    $gateScript = Join-Path $repoRoot "scripts\check_release_legal_gates.py"
    if (-not (Test-Path $gateScript)) {
        throw "Expected release legal gate script at '$gateScript'."
    }

    $pythonExe = Resolve-PythonExe
    Write-Host "Running strict release legal gate..."
    $gateArgs = @("--artifact-scope", "server", "--no-generate", "--strict-unknown-license")
    if ($AllowOpenReleaseBlockers) {
        $gateArgs += "--allow-open-release-blockers"
    }
    & $pythonExe $gateScript @gateArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Release legal gate failed. Resolve open blockers before Windows server release publishing."
    }
}

function Invoke-OfficialBuildAuthorizationGate {
    if ($Configuration -ne "Release") {
        return
    }

    $gateScript = Join-Path $repoRoot "scripts\check_official_build_authorization.py"
    if (-not (Test-Path $gateScript)) {
        throw "Expected official build authorization script at '$gateScript'."
    }

    $pythonExe = Resolve-PythonExe
    Write-Host "Checking official build authorization..."
    & $pythonExe $gateScript "--required" "--artifact-profile" $releaseSettings.ArtifactId
    if ($LASTEXITCODE -ne 0) {
        throw "Official build authorization failed. Complete the SignToROSS/OpenSign build-access agreement before Windows server release publishing."
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

Write-Host "Using release profile: $($releaseSettings.ReleaseProfile)"
Write-Host "Using Windows app version: $appVersion"
Write-Host "Using backend requirements: $($releaseSettings.RequirementsFile)"
Write-Host "Using backend runtime spec: $backendRuntimeSpec"
if ($IncludeCognee -or ("$env:AUTOYOU_INCLUDE_COGNEE".Trim().ToLower() -in @("1", "true", "yes", "on"))) {
    Write-Host "Including optional Cognee memory backend in packaged runtime."
}

Invoke-OfficialBuildAuthorizationGate

if (-not $SkipBackend) {
    & (Join-Path $scriptRoot "build-backend.ps1") -Clean:$Clean -BackendCompiler $BackendCompiler -IncludeCognee:$IncludeCognee -Requirements $releaseSettings.Requirements -Version $appVersion -JobCount $JobCount
    if ($LASTEXITCODE -ne 0) {
        throw "Backend build failed."
    }
}

if (-not (Test-Path $backendRoot)) {
    throw "Expected packaged backend at '$backendRoot'. Run build-backend.ps1 first."
}

Assert-NativeRemoteDesktopBackendCurrent -BundleRoot $backendRoot
Assert-AdkBrowserAssetsPresent -BundleRoot $backendRoot
Assert-EmotiVoiceRuntimePresent -BundleRoot $backendRoot

Write-Host "Publishing AutoYou tray host..."
Remove-Item -LiteralPath $publishRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $publishRoot | Out-Null

function Resolve-DotnetSdk {
    $candidates = [System.Collections.Generic.List[string]]::new()
    $configured = "$env:AUTOYOU_DOTNET_EXE".Trim()
    if ($configured) {
        $candidates.Add($configured)
    }

    # Prefer the repository-local SDK used by the validated Windows AMD build
    # profile. The machine may have the .NET runtime on PATH without an SDK.
    $localToolsRoot = Join-Path $repoRoot ".run"
    $currentSdk = Join-Path $localToolsRoot "dotnet-sdk-current\dotnet.exe"
    if (Test-Path -LiteralPath $currentSdk) {
        $candidates.Add($currentSdk)
    }
    if (Test-Path -LiteralPath $localToolsRoot) {
        $versionedSdks = Get-ChildItem -LiteralPath $localToolsRoot -Directory -Filter "dotnet-sdk-*" |
            Sort-Object Name -Descending
        foreach ($sdkDirectory in $versionedSdks) {
            $candidates.Add((Join-Path $sdkDirectory.FullName "dotnet.exe"))
        }
    }

    foreach ($candidate in @(
        (Join-Path $env:LocalAppData "Microsoft\dotnet\dotnet.exe"),
        (Join-Path $env:UserProfile "AppData\Local\Microsoft\dotnet\dotnet.exe"),
        "${env:ProgramFiles}\dotnet\dotnet.exe"
    )) {
        if ($candidate) {
            $candidates.Add($candidate)
        }
    }
    $pathCommand = Get-Command dotnet -CommandType Application -ErrorAction SilentlyContinue
    if ($pathCommand) {
        $candidates.Add($pathCommand.Source)
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            continue
        }
        try {
            $sdks = @(& $candidate --list-sdks 2>$null)
            if ($sdks.Count -gt 0) {
                return (Resolve-Path -LiteralPath $candidate).Path
            }
        } catch {
            # Try the next candidate. A runtime-only dotnet must not silently
            # trigger the backend-only fallback for a release build.
        }
    }

    throw "A .NET SDK was not found. Install .NET 8 SDK or set AUTOYOU_DOTNET_EXE to a repo-local SDK."
}

$dotnetExe = Resolve-DotnetSdk

$hasDotnetSdk = $false
try {
    $sdkList = & $dotnetExe --list-sdks 2>$null
    if ($sdkList -and $sdkList.Count -gt 0) {
        $hasDotnetSdk = $true
    }
} catch {}

if ($hasDotnetSdk -and (Test-Path $hostProject)) {
    $publishArguments = @(
        "publish",
        $hostProject,
        "-c", $Configuration,
        "-r", "win-x64",
        "--self-contained", "true",
        "-p:Platform=x64",
        "-p:ContinuousIntegrationBuild=true",
        "-p:DebugType=none",
        "-p:DebugSymbols=false",
        "-p:Deterministic=true",
        "-p:Version=$appVersion",
        "-p:AssemblyVersion=$appVersion",
        "-p:FileVersion=$appVersion",
        "-p:InformationalVersion=$appVersion",
        "-p:PathMap=$repoRoot=/_/AutoYou",
        "-p:PublishDir=$publishRoot\"
    )

    & $dotnetExe @publishArguments
    if ($LASTEXITCODE -ne 0) {
        throw "WinUI desktop host publish failed."
    }
    $publishedAssetsDir = Join-Path $publishRoot "Assets"
    New-Item -ItemType Directory -Force -Path $publishedAssetsDir | Out-Null
    Copy-Item (Join-Path $repoRoot "assets\logo.ico") (Join-Path $publishedAssetsDir "logo.ico") -Force
    Assert-HostPublishPresent -PublishRoot $publishRoot
    $hostPublishSucceeded = $true
    $hostExecutableHash = (Get-FileHash -Algorithm SHA256 (Join-Path $publishRoot "AutoYou.exe")).Hash
} elseif (-not $hasDotnetSdk) {
    throw "Resolved dotnet executable '$dotnetExe' does not expose an SDK. Refusing to package a backend-only Windows release."
} else {
    throw "Expected WinUI host project at '$hostProject'."
}

Start-Sleep -Milliseconds 500
$publishedBackendRoot = Join-Path $publishRoot "Backend"
New-Item -ItemType Directory -Force -Path $publishedBackendRoot | Out-Null
$pythonExe = Resolve-PythonExe
$fastCopyScript = Join-Path $repoRoot "scripts\copy_payload_fast.py"
& $pythonExe $fastCopyScript $backendRoot $publishedBackendRoot
if (-not $hostPublishSucceeded) {
    & $pythonExe $fastCopyScript $backendRoot $publishRoot
} else {
    $finalHostExecutable = Join-Path $publishRoot "AutoYou.exe"
    $finalHostHash = (Get-FileHash -Algorithm SHA256 $finalHostExecutable).Hash
    if ($finalHostHash -ne $hostExecutableHash) {
        throw "The packaged backend overwrote the native tray host executable at '$finalHostExecutable'."
    }
    Assert-HostPublishPresent -PublishRoot $publishRoot
}
Assert-NativeRemoteDesktopBackendCurrent -BundleRoot $publishedBackendRoot
Assert-AdkBrowserAssetsPresent -BundleRoot $publishedBackendRoot
Assert-EmotiVoiceRuntimePresent -BundleRoot $publishedBackendRoot

$backendExe = Resolve-BackendExecutablePath -BackendRoot $publishedBackendRoot
if (-not $backendExe) {
    throw "Expected packaged backend executable at '$publishedBackendRoot\\AutoYou.exe' (or legacy AutoYouServer.exe / AutoYouDevServer.exe)."
}

Copy-ReleaseLegalBundle -ArtifactId $releaseSettings.ArtifactId -TargetPath (Join-Path $publishRoot "Legal") -Generate
Copy-ReleaseLegalBundle -ArtifactId $releaseSettings.ArtifactId -TargetPath (Join-Path $publishedBackendRoot "Legal")
Write-ReleaseProfileMetadata -TargetPath (Join-Path $publishRoot "release-profile.json") -Settings $releaseSettings -Version $appVersion
Write-ReleaseProfileMetadata -TargetPath (Join-Path $publishedBackendRoot "release-profile.json") -Settings $releaseSettings -Version $appVersion

Invoke-StrictReleaseLegalGate

Write-Host "Desktop publish ready at $publishRoot"
