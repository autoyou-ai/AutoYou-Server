# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$NuitkaDiagnostics,
    [ValidateSet("clang", "msvc", "zig")]
    [string]$BackendCompiler = "clang",
    [ValidateSet("binary-default", "full", "source-full", "connector-full", "training-full", "base", "server-macos")]
    [string]$Requirements = "binary-default",
    [switch]$IncludeCognee,
    [switch]$DesktopV2,
    [switch]$AcceptTerms,
    [switch]$SkipLocalBuildAcknowledgement,
    [string]$Version,
    [int]$JobCount = 0  # 0 = auto-detect CPU cores
)

$ErrorActionPreference = "Stop"

function Get-Sha256Hex {
    param([string]$Path)

    $stream = [System.IO.File]::OpenRead($Path)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace("-", "")
    }
    finally {
        $sha256.Dispose()
        $stream.Dispose()
    }
}

# Auto-detect CPU cores if JobCount not specified
if ($JobCount -le 0) {
    $JobCount = (Get-CimInstance -ClassName Win32_Processor).NumberOfLogicalProcessors
    if (-not $JobCount) { $JobCount = 8 }
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path
$defaultPythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"
$backendRuntimePinPackages = @("google-adk", "google-genai", "google-cloud-aiplatform", "fastapi", "sqlalchemy")

if (-not $SkipLocalBuildAcknowledgement) {
    $pythonCommand = if (Test-Path $defaultPythonExe) {
        $defaultPythonExe
    } else {
        $candidate = Get-Command python -ErrorAction SilentlyContinue
        if (-not $candidate) { $candidate = Get-Command py -ErrorAction SilentlyContinue }
        if (-not $candidate) { throw "Could not find Python to record the local build license acknowledgment." }
        $candidate.Source
    }
    $acknowledgementArguments = @((Join-Path $repoRoot "scripts\acknowledge_local_build.py"))
    if ($AcceptTerms) { $acknowledgementArguments += "--accept-terms" }
    & $pythonCommand @acknowledgementArguments
    if ($LASTEXITCODE -ne 0) { throw "Local build license acknowledgment failed." }
}

function Resolve-BackendFileVersion {
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
        return $null
    }

    $parts = @($candidate.Split(".") | ForEach-Object { [int]$_ })
    if ($parts.Count -lt 3 -or $parts.Count -gt 4) {
        throw "Windows backend file version must have 3 or 4 numeric parts: $candidate"
    }
    while ($parts.Count -lt 4) {
        $parts += 0
    }
    return ($parts -join ".")
}

$backendFileVersion = Resolve-BackendFileVersion -RequestedVersion $Version

function Resolve-BackendRequirementsFile {
    param(
        [string]$RepoRoot,
        [string]$Requirements
    )

    switch ($Requirements) {
        "full" { return (Join-Path $RepoRoot "requirements.txt") }
        "source-full" { return (Join-Path $RepoRoot "requirements.txt") }
        "connector-full" { return (Join-Path $RepoRoot "requirements.txt") }
        "base" { return (Join-Path $RepoRoot "requirements\base.txt") }
        "server-macos" { return (Join-Path $RepoRoot "requirements\server-macos.txt") }
        default { return (Join-Path $RepoRoot "requirements\$Requirements.txt") }
    }
}

function Test-Python312Executable {
    param(
        [string]$Candidate
    )

    if ([string]::IsNullOrWhiteSpace($Candidate) -or
        (-not (Test-Path -LiteralPath $Candidate -PathType Leaf))) {
        return $false
    }

    try {
        $version = (& $Candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null | Select-Object -First 1)
        return $version -and ($version.Trim() -eq "3.12")
    }
    catch {
        return $false
    }
}

function Resolve-Python312Executable {
    $configuredPython = "$env:AUTOYOU_BUILD_PYTHON312".Trim()
    if ($configuredPython) {
        if (-not (Test-Python312Executable -Candidate $configuredPython)) {
            throw "AUTOYOU_BUILD_PYTHON312 must point to a working Python 3.12 executable. Received '$configuredPython'."
        }
        return (Resolve-Path -LiteralPath $configuredPython).Path
    }

    $candidates = [System.Collections.Generic.List[string]]::new()
    $candidates.Add("C:\Python312\python.exe")
    if ($env:LOCALAPPDATA) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"))
    }

    $python312Command = Get-Command "python3.12.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($python312Command) {
        $candidates.Add($python312Command.Source)
    }

    $pyLauncher = Get-Command "py.exe" -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pyLauncher) {
        try {
            $launcherPython = (& $pyLauncher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null | Select-Object -First 1)
            if ($launcherPython) {
                $candidates.Add($launcherPython.Trim())
            }
        }
        catch {
            # Continue through the explicit filesystem candidates below.
        }
    }

    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-Python312Executable -Candidate $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    return $null
}

$backendRuntimeRequirementsFile = Resolve-BackendRequirementsFile -RepoRoot $repoRoot -Requirements $Requirements
if (-not (Test-Path $backendRuntimeRequirementsFile)) {
    throw "Requirements profile '$Requirements' was requested, but '$backendRuntimeRequirementsFile' was not found."
}
$cogneeRequirementsFile = Join-Path $repoRoot "requirements\cognee.txt"
$realtimeSttRuntimeRequirementsFile = Join-Path $repoRoot "requirements\realtimestt-runtime.txt"
$includeCogneeEnv = "$env:AUTOYOU_INCLUDE_COGNEE".Trim().ToLower()
$includeCogneeRuntime = $IncludeCognee -or ($includeCogneeEnv -in @("1", "true", "yes", "on"))
$requirementsIncludesVoice = $Requirements -in @("full", "source-full", "connector-full", "training-full")
$requirementsIncludesTuning = $Requirements -eq "training-full"

if (-not (Test-Path $defaultPythonExe)) {
    throw "Expected build Python at '$defaultPythonExe'."
}

$pythonExe = $defaultPythonExe
$defaultPythonVersion = (& $defaultPythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null | Select-Object -First 1)
if ($defaultPythonVersion) {
    $defaultPythonVersion = $defaultPythonVersion.Trim()
}

if ($defaultPythonVersion -eq "3.13") {
    $python312Exe = Resolve-Python312Executable
    if ($python312Exe) {
        $buildVenvRoot = Join-Path $repoRoot ".venv-build312"
        $buildPythonExe = Join-Path $buildVenvRoot "Scripts\python.exe"
        if (-not (Test-Path $buildPythonExe)) {
            Write-Warning "Using dedicated Python 3.12 build virtualenv for Windows Nuitka packaging from '$python312Exe'."
            & $python312Exe -m venv $buildVenvRoot
            if (($LASTEXITCODE -ne 0) -or (-not (Test-Path $buildPythonExe))) {
                throw "Failed to create Python 3.12 build virtualenv at '$buildVenvRoot'."
            }
        }

        $pythonExe = $buildPythonExe
    }
    else {
        Write-Warning "Python 3.13 is active and no Python 3.12 build interpreter was found. Set AUTOYOU_BUILD_PYTHON312 to a Python 3.12 executable for the most compatible Nuitka toolchain."
    }
}

$buildPythonVersion = (& $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null | Select-Object -First 1)
if (-not $buildPythonVersion) {
    throw "Could not determine the build Python version from '$pythonExe'."
}
$buildPythonVersion = $buildPythonVersion.Trim()

function Get-BackendRuntimePins {
    param(
        [string]$PythonExe,
        [string]$RepoRoot,
        [string]$RequirementsFile,
        [string[]]$PackageNames
    )

    $scriptPath = Join-Path $RepoRoot "scripts\read_backend_runtime_pins.py"
    if (-not (Test-Path $scriptPath)) {
        throw "Expected backend runtime pin reader at '$scriptPath'."
    }

    $arguments = @(
        $scriptPath,
        "--requirements-file", $RequirementsFile,
        "--format", "json",
        "--packages"
    ) + $PackageNames

    $rawOutput = & $PythonExe @arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $message = ($rawOutput | Out-String).Trim()
        throw "Failed to resolve backend runtime pins from '$RequirementsFile': $message"
    }

    $parsed = (($rawOutput -join "`n") | ConvertFrom-Json)
    $pins = @{}
    foreach ($property in $parsed.PSObject.Properties) {
        $pins[[string]$property.Name] = [string]$property.Value
    }

    return $pins
}

function Format-BackendRuntimePins {
    param(
        [hashtable]$PinnedPackages,
        [string[]]$PackageNames
    )

    $orderedNames = @()
    foreach ($name in $PackageNames) {
        if ($PinnedPackages.ContainsKey($name)) {
            $orderedNames += $name
        }
    }

    if (-not $orderedNames) {
        $orderedNames = @($PinnedPackages.Keys | Sort-Object)
    }

    return (($orderedNames | ForEach-Object { "$_==$($PinnedPackages[$_])" }) -join ", ")
}

$backendRuntimePins = Get-BackendRuntimePins -PythonExe $pythonExe -RepoRoot $repoRoot -RequirementsFile $backendRuntimeRequirementsFile -PackageNames $backendRuntimePinPackages

$sitePackagesRoot = (& $pythonExe -c "import sysconfig; print(sysconfig.get_path('purelib'))" 2>$null | Select-Object -First 1)
if (-not $sitePackagesRoot) {
    throw "Could not determine Python site-packages for '$pythonExe'."
}
$sitePackagesRoot = $sitePackagesRoot.Trim()

$stdlibRoot = (& $pythonExe -c "import sysconfig; print(sysconfig.get_path('stdlib'))" 2>$null | Select-Object -First 1)
if (-not $stdlibRoot) {
    throw "Could not determine Python stdlib for '$pythonExe'."
}
$stdlibRoot = $stdlibRoot.Trim()

$pythonBasePrefix = (& $pythonExe -c "import sys; print(sys.base_prefix)" 2>$null | Select-Object -First 1)
if (-not $pythonBasePrefix) {
    throw "Could not determine Python base prefix for '$pythonExe'."
}
$pythonBasePrefix = $pythonBasePrefix.Trim()

$pythonDllsRoot = Join-Path $pythonBasePrefix "DLLs"
if (-not (Test-Path $pythonDllsRoot)) {
    throw "Expected Python DLL directory at '$pythonDllsRoot'."
}

$runtimeModuleBuilderScript = Join-Path $repoRoot "scripts\build_packaged_runtime_modules.py"
if (-not (Test-Path $runtimeModuleBuilderScript)) {
    throw "Expected packaged runtime module builder at '$runtimeModuleBuilderScript'."
}
$packagedGuidesScript = Join-Path $repoRoot "scripts\prepare_packaged_guides.py"
if (-not (Test-Path $packagedGuidesScript)) {
    throw "Expected packaged guide staging helper at '$packagedGuidesScript'."
}
$runtimeSitePackagesPrunerScript = Join-Path $repoRoot "scripts\prune_runtime_site_packages_to_requirements.py"
if (-not (Test-Path $runtimeSitePackagesPrunerScript)) {
    throw "Expected runtime site-packages pruner at '$runtimeSitePackagesPrunerScript'."
}
$runtimeDependencyReconcileScript = Join-Path $repoRoot "scripts\reconcile_python_runtime_env.py"
if (-not (Test-Path $runtimeDependencyReconcileScript)) {
    throw "Expected runtime dependency reconcile helper at '$runtimeDependencyReconcileScript'."
}
$realtimeSttRuntimeInstallerScript = Join-Path $repoRoot "scripts\install_realtimestt_runtime.py"
if (-not (Test-Path $realtimeSttRuntimeInstallerScript)) {
    throw "Expected RealtimeSTT runtime installer at '$realtimeSttRuntimeInstallerScript'."
}
$nonCommercialAssetPrunerScript = Join-Path $repoRoot "scripts\prune_noncommercial_release_assets.py"
if (-not (Test-Path $nonCommercialAssetPrunerScript)) {
    throw "Expected non-commercial asset pruner at '$nonCommercialAssetPrunerScript'."
}
$legalCopyScript = Join-Path $repoRoot "scripts\copy_release_legal_artifacts.py"
if (-not (Test-Path $legalCopyScript)) {
    throw "Expected release legal copy script at '$legalCopyScript'."
}

$artifactRoot = Join-Path $scriptRoot "artifacts"
$backendArtifactRoot = Join-Path $artifactRoot "backend"
$nuitkaOutputRoot = Join-Path $backendArtifactRoot "nuitka"
$finalBackendRoot = Join-Path $backendArtifactRoot "AutoYouServer"
$runtimeGuidesRoot = Join-Path $backendArtifactRoot "runtime-guides"
$downloadRoot = Join-Path $artifactRoot "downloads"
$nodeRuntimeRoot = Join-Path $artifactRoot "node-runtime"
$playwrightRoot = Join-Path $artifactRoot "playwright-browsers"
$volatileNodeDataPatterns = @(
    "node/whatsapp/.wwebjs_auth/*",
    "node/whatsapp/.wwebjs_auth/**/*",
    "node/whatsapp/.wwebjs_cache/*",
    "node/whatsapp/.wwebjs_cache/**/*"
)
$mutableRuntimeDataPatterns = @(
    "config.keystore.enc",
    "config.encrypted",
    "config.encrypted.bak",
    "agent_install_registry.json",
    "agent_frontends_registry.json",
    "login_ui_state.db",
    "page_feed.db",
    "sessions.db",
    "sessions.db.bak",
    "autoyou_agents/.adk/*",
    "autoyou_agents/.adk/**/*",
    "autoyou_agents/notes_agent/*.db",
    "autoyou_agents/notes_agent/*.sqlite",
    "autoyou_agents/notes_agent/*.sqlite3",
    "autoyou_agents/notes_agent/media/*",
    "autoyou_agents/notes_agent/media/**/*",
    "autoyou_agents/notes_agent/autoyou_notes_agent/*",
    "autoyou_agents/notes_agent/autoyou_notes_agent/**/*"
)

if ($Clean) {
    Remove-Item $backendArtifactRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $nodeRuntimeRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $playwrightRoot -Recurse -Force -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path $artifactRoot, $backendArtifactRoot, $nuitkaOutputRoot, $downloadRoot | Out-Null

function Get-VswherePath {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        return $vswhere
    }

    return $null
}

function Get-LatestVisualStudioInstance {
    param(
        [string]$RequiredComponent
    )

    $vswhere = Get-VswherePath
    if (-not $vswhere) {
        return $null
    }

    $arguments = @("-products", "*", "-latest", "-format", "json")
    if ($RequiredComponent) {
        $arguments += @("-requires", $RequiredComponent)
    }

    $json = & $vswhere @arguments
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($json)) {
        return $null
    }

    $instances = $json | ConvertFrom-Json
    return @($instances) | Select-Object -First 1
}

function Get-VisualStudioClangInstallCommand {
    param(
        [string]$InstallPath
    )

    $setupExe = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\setup.exe"
    return "& `"$setupExe`" modify --installPath `"$InstallPath`" --add Microsoft.VisualStudio.Component.VC.Llvm.Clang --add Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset --quiet --norestart"
}

function Resolve-VisualStudioClangBin {
    $instance = Get-LatestVisualStudioInstance -RequiredComponent "Microsoft.VisualStudio.Component.VC.Llvm.Clang"
    if ($instance) {
        $visualStudioVersion = [version]$instance.installationVersion
        if ($visualStudioVersion.Major -ge 17) {
            $vsCandidateDirectories = @(
                (Join-Path $instance.installationPath "VC\Tools\Llvm\x64\bin"),
                (Join-Path $instance.installationPath "VC\Tools\Llvm\bin")
            )
            foreach ($directory in $vsCandidateDirectories) {
                if (Test-Path (Join-Path $directory "clang-cl.exe")) {
                    return $directory
                }
            }
        }
    }

    $fallbackInstance = Get-LatestVisualStudioInstance -RequiredComponent ""
    $installPath = if ($fallbackInstance) { $fallbackInstance.installationPath } else { "C:\Program Files\Microsoft Visual Studio\2022\Community" }
    $repairCommand = Get-VisualStudioClangInstallCommand -InstallPath $installPath
    throw "LLVM/Clang compiler not found. Install it via 'winget install LLVM.LLVM' or using elevated PowerShell: $repairCommand"
}

function Get-CapturedCommandOutput {
    param(
        $ErrorRecord
    )

    if ($null -eq $ErrorRecord -or $null -eq $ErrorRecord.Exception) {
        return ""
    }

    if ($ErrorRecord.Exception.Data.Contains("CommandOutput")) {
        return [string]$ErrorRecord.Exception.Data["CommandOutput"]
    }

    return ""
}

function Test-ClangAllocationFailure {
    param(
        [string]$CommandOutput
    )

    if ([string]::IsNullOrWhiteSpace($CommandOutput)) {
        return $false
    }

    return $CommandOutput -match "(?im)^Allocation failed$"
}

function Test-ClangFrontendCrash {
    param(
        [string]$CommandOutput
    )

    if ([string]::IsNullOrWhiteSpace($CommandOutput)) {
        return $false
    }

    return (Test-ClangAllocationFailure -CommandOutput $CommandOutput) -or
        ($CommandOutput -match "(?im)clang-cl: error: clang frontend command failed due to signal") -or
        ($CommandOutput -match "(?im)PLEASE ATTACH THE FOLLOWING FILES TO THE BUG REPORT")
}

function Test-MsvcHeapFailure {
    param(
        [string]$CommandOutput
    )

    if ([string]::IsNullOrWhiteSpace($CommandOutput)) {
        return $false
    }

    return ($CommandOutput -match "(?im)fatal error C1002: compiler is out of heap space") -or
        ($CommandOutput -match "(?im)fatal error C1060: compiler is out of heap space")
}

function Test-MsvcToolchainFailure {
    param(
        [string]$CommandOutput
    )

    if ([string]::IsNullOrWhiteSpace($CommandOutput)) {
        return $false
    }

    return ($CommandOutput -match "(?im)fatal error C1356: unable to find mspdbcore\.dll") -or
        ($CommandOutput -match "(?im)mspdbcore\.dll")
}

function Test-NuitkaUnexpectedPdbFailure {
    param(
        [string]$CommandOutput
    )

    if ([string]::IsNullOrWhiteSpace($CommandOutput)) {
        return $false
    }

    return $CommandOutput -match "(?im)FATAL: Error, unwanted '\.pdb' file"
}

function Test-NuitkaCompilerMismatchFailure {
    param(
        [string]$CommandOutput
    )

    if ([string]::IsNullOrWhiteSpace($CommandOutput)) {
        return $false
    }

    return $CommandOutput -match "(?im)gcc\.exe: error: unrecognized command-line option '-Xclang'"
}

function Get-NuitkaCompilerConfiguration {
    param(
        [ValidateSet("clang", "msvc", "zig")]
        [string]$Compiler,
        [string]$PythonVersion
    )

    $environment = @{}
    $effectiveCompiler = $Compiler

    if ($Compiler -eq "zig") {
        $arguments = @("--zig", "--assume-yes-for-downloads")
        Write-Host "Using Zig backend C compiler."
    }
    elseif ($Compiler -eq "clang") {
        try {
            $clangBin = Resolve-VisualStudioClangBin
            $environment["PATH"] = "$clangBin;$env:PATH"
            $arguments = @("--clang")
            Write-Host "Using Visual Studio LLVM/Clang from $clangBin"
        }
        catch {
            if ([version]$PythonVersion -lt [version]"3.13") {
                Write-Warning "Visual Studio 2022 LLVM/Clang component not found. Falling back to Nuitka's supported MinGW64 compiler for Python $PythonVersion."
                $arguments = @("--mingw64", "--assume-yes-for-downloads")
                $effectiveCompiler = "mingw64"
            }
            else {
                Write-Warning "Visual Studio 2022 LLVM/Clang component not found. Falling back to Zig backend C compiler for Python $PythonVersion."
                $arguments = @("--zig", "--assume-yes-for-downloads")
                $effectiveCompiler = "zig"
            }
        }
    }
    else {
        $arguments = @("--msvc=latest")
        Write-Host "Using MSVC backend compiler."
    }

    return [pscustomobject]@{
        Compiler = $effectiveCompiler
        Environment = $environment
        Arguments = $arguments
    }
}

function Resolve-BackendLegalArtifactId {
    param(
        [string]$Requirements
    )

    if ($Requirements -in @("full", "source-full", "connector-full", "training-full")) {
        return "autoyou-server-windows-connector-full"
    }

    return "autoyou-server-windows-default"
}

function Invoke-CheckedCommand {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [hashtable]$Environment = @{},
        [switch]$CaptureOutput
    )

    $previous = @{}
    foreach ($pair in $Environment.GetEnumerator()) {
        $previous[$pair.Key] = [Environment]::GetEnvironmentVariable($pair.Key)
        [Environment]::SetEnvironmentVariable($pair.Key, $pair.Value)
    }

    try {
        if ($CaptureOutput) {
            $capturedOutput = @()
            $exitCode = 0
            $previousErrorActionPreference = $ErrorActionPreference
            $nativePreferenceExists = $null -ne (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue)
            if ($nativePreferenceExists) {
                $previousNativeCommandPreference = $Global:PSNativeCommandUseErrorActionPreference
                $Global:PSNativeCommandUseErrorActionPreference = $false
            }
            try {
                $ErrorActionPreference = "Continue"
                & $FilePath @Arguments 2>&1 | Tee-Object -Variable capturedOutput
                $exitCode = $LASTEXITCODE
            }
            finally {
                $ErrorActionPreference = $previousErrorActionPreference
                if ($nativePreferenceExists) {
                    $Global:PSNativeCommandUseErrorActionPreference = $previousNativeCommandPreference
                }
            }
            if ($exitCode -ne 0) {
                $exception = New-Object System.Exception("Command failed with exit code ${exitCode}: $FilePath $($Arguments -join ' ')")
                $exception.Data["CommandOutput"] = (($capturedOutput | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine)
                throw $exception
            }
            return @($capturedOutput)
        }

        $previousErrorActionPreference = $ErrorActionPreference
        $nativePreferenceExists = $null -ne (Get-Variable -Name PSNativeCommandUseErrorActionPreference -Scope Global -ErrorAction SilentlyContinue)
        if ($nativePreferenceExists) {
            $previousNativeCommandPreference = $Global:PSNativeCommandUseErrorActionPreference
            $Global:PSNativeCommandUseErrorActionPreference = $false
        }
        try {
            $ErrorActionPreference = "Continue"
            & $FilePath @Arguments
            if ($LASTEXITCODE -ne 0) {
                throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
            }
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
            if ($nativePreferenceExists) {
                $Global:PSNativeCommandUseErrorActionPreference = $previousNativeCommandPreference
            }
        }
    }
    finally {
        foreach ($pair in $Environment.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable($pair.Key, $previous[$pair.Key])
        }
    }
}

function Invoke-NuitkaBuildWithRetry {
    param(
        [string]$PythonExe,
        [string[]]$Arguments,
        [hashtable]$Environment = @{},
        [int]$RequestedJobCount,
        [string]$OutputRoot
    )

    $attemptJobCount = [Math]::Max($RequestedJobCount, 1)
    $minimumJobCount = 1

    while ($true) {
        $attemptArguments = foreach ($argument in $Arguments) {
            if ($argument -like "--jobs=*") {
                "--jobs=$attemptJobCount"
            }
            else {
                $argument
            }
        }

        Write-Host "Invoking Nuitka with --jobs=$attemptJobCount..."

        try {
            Invoke-CheckedCommand -FilePath $PythonExe -Arguments $attemptArguments -Environment $Environment -CaptureOutput | Out-Null
            return $attemptJobCount
        }
        catch {
            $capturedOutput = Get-CapturedCommandOutput -ErrorRecord $_
            if ($capturedOutput) {
                Write-Host "Nuitka compilation error output:`n$capturedOutput"
            }

            $isMemoryError = ($_.Exception.Message -match "MemoryError") -or ($capturedOutput -match "MemoryError")
            $isClangAllocationFailure = Test-ClangAllocationFailure -CommandOutput $capturedOutput
            $isUnexpectedPdbFailure = Test-NuitkaUnexpectedPdbFailure -CommandOutput $capturedOutput
            $isCompilerMismatchFailure = Test-NuitkaCompilerMismatchFailure -CommandOutput $capturedOutput
            $shouldReduceJobCount = (-not $isUnexpectedPdbFailure) -and
                (-not $isCompilerMismatchFailure) -and
                ($isMemoryError -or $isClangAllocationFailure -or ($attemptJobCount -gt 8)) -and
                $attemptJobCount -gt $minimumJobCount

            if (-not $shouldReduceJobCount) {
                throw
            }

            $nextJobCount = [Math]::Max([int][Math]::Floor($attemptJobCount / 2), $minimumJobCount)
            if ($nextJobCount -ge $attemptJobCount) {
                throw
            }

            if ($isClangAllocationFailure) {
                Write-Warning "LLVM/Clang ran out of memory while compiling generated C code with --jobs=$attemptJobCount. Retrying with --jobs=$nextJobCount."
            }
            else {
                Write-Warning "Nuitka ran out of memory with --jobs=$attemptJobCount. Retrying with --jobs=$nextJobCount."
            }
            Remove-Item $OutputRoot -Recurse -Force -ErrorAction SilentlyContinue
            New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
            $attemptJobCount = $nextJobCount
        }
    }
}

function Install-PortableNodeRuntime {
    param(
        [string]$DestinationRoot,
        [string]$CacheRoot
    )

    $cachedNode = Join-Path $DestinationRoot "node.exe"
    if (Test-Path $cachedNode) {
        try {
            $cachedVersion = (& $cachedNode --version 2>$null | Select-Object -First 1)
            if ([version]$cachedVersion.TrimStart('v') -ge [version]'22.12.0') {
                return
            }
        } catch {}
    }

    $index = Invoke-RestMethod -Uri "https://nodejs.org/dist/index.json"
    $release = $index |
    Where-Object { $_.lts -and ($_.files -contains "win-x64-zip") } |
    Select-Object -First 1

    if (-not $release) {
        throw "Unable to resolve the latest Node.js LTS win-x64 zip release."
    }

    $version = [string]$release.version
    $zipName = "node-$version-win-x64.zip"
    $zipPath = Join-Path $CacheRoot $zipName
    $extractRoot = Join-Path $CacheRoot "node-extract"
    $zipUrl = "https://nodejs.org/dist/$version/$zipName"

    if (-not (Test-Path $zipPath)) {
        Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath
    }

    Remove-Item $extractRoot -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive -Path $zipPath -DestinationPath $extractRoot -Force

    $expandedDirectory = Get-ChildItem $extractRoot -Directory | Select-Object -First 1
    if (-not $expandedDirectory) {
        throw "Node.js archive did not contain an extracted directory."
    }

    Remove-Item $DestinationRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $DestinationRoot | Out-Null
    Copy-Item (Join-Path $expandedDirectory.FullName "*") $DestinationRoot -Recurse -Force
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

function Assert-AdkBrowserBundle {
    param(
        [string]$BackendRoot
    )

    $browserRoot = Get-AdkBrowserRoot -BackendRoot $BackendRoot
    $requiredFiles = @(
        (Join-Path $browserRoot "index.html"),
        (Join-Path $browserRoot "assets\audio-processor.js"),
        (Join-Path $browserRoot "assets\config\runtime-config.json")
    )

    foreach ($filePath in $requiredFiles) {
        if (-not (Test-Path $filePath)) {
            throw "Expected Google ADK browser asset '$filePath' was not found in the packaged backend."
        }
    }

    if (-not (Get-ChildItem $browserRoot -Filter "main-*.js" -File -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        throw "Expected a Google ADK browser main-*.js bundle under '$browserRoot'."
    }

    if (-not (Get-ChildItem $browserRoot -Filter "styles-*.css" -File -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        throw "Expected a Google ADK browser styles-*.css bundle under '$browserRoot'."
    }
}

function Vendor-AdkBrowserFonts {
    param(
        [string]$BackendRoot,
        [string]$CacheRoot
    )

    $browserRoot = Get-AdkBrowserRoot -BackendRoot $BackendRoot
    $indexPath = Join-Path $browserRoot "index.html"
    if (-not (Test-Path $indexPath)) {
        throw "Cannot vendor Google ADK browser fonts because '$indexPath' was not found."
    }

    $fontOutputRoot = Join-Path $browserRoot "vendor-fonts"
    $fontCacheRoot = Join-Path $CacheRoot "adk-web-fonts"
    $html = Get-Content $indexPath -Raw
    $fontUrls = [regex]::Matches($html, 'https://fonts\.gstatic\.com/[^)"''\s]+') |
    ForEach-Object { $_.Value } |
    Select-Object -Unique

    foreach ($fontUrl in $fontUrls) {
        $uri = [Uri]$fontUrl
        $relativeSegments = $uri.AbsolutePath.TrimStart('/').Split('/')
        $cachePath = $fontCacheRoot
        $outputPath = $fontOutputRoot

        foreach ($segment in $relativeSegments) {
            $cachePath = Join-Path $cachePath $segment
            $outputPath = Join-Path $outputPath $segment
        }

        $cacheParent = Split-Path -Parent $cachePath
        $outputParent = Split-Path -Parent $outputPath
        New-Item -ItemType Directory -Force -Path $cacheParent, $outputParent | Out-Null

        if (-not (Test-Path $cachePath)) {
            Invoke-WebRequest -Uri $fontUrl -OutFile $cachePath
        }

        Copy-Item $cachePath $outputPath -Force

        $relativeUrl = "./vendor-fonts/" + (($uri.AbsolutePath.TrimStart('/')) -replace '\\', '/' -replace '^/+', '')
        $html = $html.Replace($fontUrl, $relativeUrl)
    }

    $html = $html -replace '(?m)^\s*<link rel="preconnect" href="https://fonts\.googleapis\.com">\r?\n?', ''
    $html = $html -replace '(?m)^\s*<link rel="preconnect" href="https://fonts\.gstatic\.com" crossorigin>\r?\n?', ''

    if ($html -match 'https://fonts\.(googleapis|gstatic)\.com') {
        throw "Google ADK browser index.html still references remote Google Fonts after vendoring."
    }

    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($indexPath, $html, $utf8NoBom)
}

function Copy-VoiceProcessingDlls {
    param(
        [string]$BackendRoot,
        [string]$PythonExe,
        [switch]$RequireFastTranscription
    )

    Write-Host "Verifying Windows native audio libraries..."

    $runtimeSitePackagesRoot = Join-Path $BackendRoot "runtime_site_packages"
    $runtimeStdlibDllRoot = Join-Path $BackendRoot "runtime_stdlib\DLLs"
    New-Item -ItemType Directory -Force -Path $runtimeStdlibDllRoot | Out-Null

    # Keep the commercial default profile free of stale optional STT DLLs if a
    # build directory is reused after a full voice build.
    if (-not $RequireFastTranscription) {
        Write-Host "CTranslate2 DLLs are not required for '$Requirements'; whisper.cpp remains the bundled STT path."
        foreach ($optionalDll in @("ctranslate2.dll", "libiomp5md.dll")) {
            foreach ($destinationRoot in @($BackendRoot, $runtimeStdlibDllRoot)) {
                Remove-Item -LiteralPath (Join-Path $destinationRoot $optionalDll) -Force -ErrorAction SilentlyContinue
            }
        }
    }

    # Get Python site-packages path for native library lookup
    $sitePkgsPath = (& $PythonExe -c "import site; print(site.getsitepackages()[0])" 2>$null | Select-Object -First 1)
    if ($sitePkgsPath) { $sitePkgsPath = $sitePkgsPath.Trim() }
    if (-not $sitePkgsPath) {
        if ($RequireFastTranscription) {
            throw "Could not determine Python site-packages; cannot verify the full voice profile's CTranslate2 runtime."
        }
        Write-Warning "Could not determine Python site-packages, skipping voice DLL verification"
        return
    }

    function Resolve-FirstExistingDllSource {
        param(
            [string[]]$Candidates
        )

        foreach ($candidate in $Candidates) {
            if ($candidate -and (Test-Path $candidate)) {
                return $candidate
            }
        }

        return $null
    }

    # The default binary profile uses the bundled whisper.cpp fallback. The
    # optional full voice profile uses the precompiled CPU libraries shipped by
    # the pinned CTranslate2 wheel; do not build or fetch these DLLs separately.
    $requiredDlls = @("msvcp140.dll", "vcruntime140.dll")
    if ($RequireFastTranscription) {
        $requiredDlls = @("ctranslate2.dll", "libiomp5md.dll") + $requiredDlls
    }

    $backendBinDir = $BackendRoot
    $ctranslate2Dir = Join-Path $sitePkgsPath "ctranslate2"
    $fastWhisperDir = Join-Path $sitePkgsPath "faster_whisper"

    $voiceDllSources = @{
        "ctranslate2.dll" = @(
            (Join-Path $runtimeSitePackagesRoot "ctranslate2\ctranslate2.dll"),
            (Join-Path $ctranslate2Dir "ctranslate2.dll")
        )
        "libiomp5md.dll" = @(
            (Join-Path $runtimeSitePackagesRoot "ctranslate2\libiomp5md.dll"),
            (Join-Path $runtimeSitePackagesRoot "torch\lib\libiomp5md.dll"),
            (Join-Path $ctranslate2Dir "libiomp5md.dll"),
            (Join-Path $sitePkgsPath "torch\lib\libiomp5md.dll")
        )
        "msvcp140.dll" = @(
            (Join-Path $backendBinDir "msvcp140.dll"),
            (Join-Path $runtimeStdlibDllRoot "msvcp140.dll"),
            (Join-Path $runtimeSitePackagesRoot "sklearn\.libs\msvcp140.dll"),
            (Join-Path $sitePkgsPath "sklearn\.libs\msvcp140.dll"),
            (Join-Path $env:WINDIR "System32\msvcp140.dll")
        )
        "vcruntime140.dll" = @(
            (Join-Path $backendBinDir "vcruntime140.dll"),
            (Join-Path $runtimeStdlibDllRoot "vcruntime140.dll"),
            (Join-Path $pythonBasePrefix "vcruntime140.dll"),
            (Join-Path $env:WINDIR "System32\vcruntime140.dll")
        )
    }

    foreach ($dllName in $requiredDlls) {
        $resolvedSource = Resolve-FirstExistingDllSource -Candidates $voiceDllSources[$dllName]
        if (-not $resolvedSource) {
            continue
        }

        foreach ($destinationRoot in @($backendBinDir, $runtimeStdlibDllRoot)) {
            $destinationPath = Join-Path $destinationRoot $dllName
            if (-not (Test-Path $destinationPath)) {
                Copy-Item $resolvedSource $destinationPath -Force -ErrorAction SilentlyContinue
                if (Test-Path $destinationPath) {
                    Write-Host "Bundled voice DLL: $dllName -> $destinationRoot"
                }
            }
        }
    }

    # Verify that required DLLs are inside the outgoing bundle, not merely
    # somewhere in the build machine's site-packages or System32.
    $missingDlls = @()
    foreach ($dll in $requiredDlls) {
        $packagedCandidates = @(
            (Join-Path $backendBinDir $dll),
            (Join-Path $runtimeStdlibDllRoot $dll)
        )

        $resolvedPath = Resolve-FirstExistingDllSource -Candidates $packagedCandidates
        if (-not $resolvedPath) {
            $missingDlls += $dll
        }
    }

    if ($missingDlls.Count -gt 0) {
        $message = "Required Windows audio libraries were not packaged: $($missingDlls -join ', ')"
        if ($RequireFastTranscription) {
            throw "$message. Rebuild the pinned CTranslate2 wheel runtime before packaging the full voice profile."
        }
        Write-Warning $message
    } else {
        Write-Host "Windows audio libraries verified present"
    }
}

function Install-WhisperCppRuntime {
    param(
        [string]$RuntimeRoot,
        [string]$PythonExe
    )

    Write-Host "Preparing whisper.cpp runtime..."
    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

    $verificationRoot = Join-Path $artifactRoot "verification"
    New-Item -ItemType Directory -Force -Path $verificationRoot | Out-Null

    $scriptPath = Join-Path $verificationRoot "prepare_whisper_runtime.py"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $repoRootPython = $repoRoot.Replace('\', '\\')
    $pythonCode = @"
from pathlib import Path
import sys

REPO_ROOT = Path(r"$repoRootPython").resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.whisper_downloader import ensure_whisper_cpp_runtime_bundle

bundle = ensure_whisper_cpp_runtime_bundle()
if bundle is None:
    raise SystemExit("whisper.cpp runtime bundle unavailable")

print(f"WHISPER_BUNDLE={Path(bundle).resolve()}")
"@

    [System.IO.File]::WriteAllText($scriptPath, $pythonCode, $utf8NoBom)

    $pythonOutput = & $PythonExe $scriptPath 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to prepare whisper.cpp runtime: $($pythonOutput -join "`n")"
    }

    $bundleLine = $pythonOutput | Where-Object { $_ -like "WHISPER_BUNDLE=*" } | Select-Object -Last 1
    if (-not $bundleLine) {
        throw "Could not determine the prepared whisper.cpp runtime bundle path. Output: $($pythonOutput -join "`n")"
    }

    $sourceRoot = $bundleLine.Substring("WHISPER_BUNDLE=".Length).Trim()
    if (-not (Test-Path $sourceRoot)) {
        throw "Prepared whisper.cpp runtime path '$sourceRoot' does not exist."
    }

    Copy-Item (Join-Path $sourceRoot "*") $RuntimeRoot -Recurse -Force

    $hasBaseLibraries = (Test-Path (Join-Path $RuntimeRoot "whisper.dll")) -and (Test-Path (Join-Path $RuntimeRoot "ggml.dll"))
    $hasCpuLibraries = (Test-Path (Join-Path $RuntimeRoot "ggml-cpu.dll")) -or (Test-Path (Join-Path $RuntimeRoot "ggml-base.dll")) -or @(Get-ChildItem -Path $RuntimeRoot -Filter "ggml-cpu-*.dll" -ErrorAction SilentlyContinue).Count -gt 0
    if (-not ($hasBaseLibraries -and $hasCpuLibraries)) {
        throw "Expected whisper runtime DLLs (whisper.dll, ggml.dll, ggml-cpu-*.dll / ggml-base.dll) were not found under '$RuntimeRoot' after bundling."
    }

    $binaryCandidates = @(
        "whisper-whisper-cli.exe",
        "whisper-cli.exe",
        "main.exe"
    )
    $resolvedBinary = $binaryCandidates |
        ForEach-Object { Join-Path $RuntimeRoot $_ } |
        Where-Object { Test-Path $_ } |
        Select-Object -First 1

    if (-not $resolvedBinary) {
        throw "Expected a whisper runtime executable under '$RuntimeRoot' but none of the known candidate names were found."
    }

    Write-Host "whisper.cpp runtime bundled from $sourceRoot"
}

function Install-TunnelmoleRuntime {
    param(
        [string]$RuntimeRoot,
        [string]$PythonExe
    )

    Write-Host "Preparing tunnelmole runtime..."
    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

    $verificationRoot = Join-Path $artifactRoot "verification"
    New-Item -ItemType Directory -Force -Path $verificationRoot | Out-Null

    $scriptPath = Join-Path $verificationRoot "prepare_tunnelmole_runtime.py"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    $repoRootPython = $repoRoot.Replace('\', '\\')
    $pythonCode = @"
from pathlib import Path
import sys

REPO_ROOT = Path(r"$repoRootPython").resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.tunnelmole_downloader import download_tunnelmole

binary = download_tunnelmole(force=True)
if binary is None:
    raise SystemExit("tunnelmole runtime binary unavailable")

print(f"TUNNELMOLE_BIN={Path(binary).resolve()}")
"@

    [System.IO.File]::WriteAllText($scriptPath, $pythonCode, $utf8NoBom)

    try {
        $pythonOutput = Invoke-CheckedCommand -FilePath $PythonExe -Arguments @($scriptPath) -CaptureOutput
    }
    catch {
        $commandOutput = $_.Exception.Data["CommandOutput"]
        if (-not $commandOutput) {
            $commandOutput = $_.Exception.Message
        }
        Write-Warning "Failed to prepare tunnelmole runtime: $commandOutput"
        Write-Warning "Continuing without bundling a standalone tunnelmole binary into the Windows runtime."
        return
    }

    $binaryLine = $pythonOutput | Where-Object { $_ -like "TUNNELMOLE_BIN=*" } | Select-Object -Last 1
    if (-not $binaryLine) {
        Write-Warning "Could not determine the prepared tunnelmole runtime path. Output: $($pythonOutput -join "`n")"
        Write-Warning "Continuing without bundling a standalone tunnelmole binary into the Windows runtime."
        return
    }

    $sourceBinary = $binaryLine.Substring("TUNNELMOLE_BIN=".Length).Trim()
    if (-not (Test-Path $sourceBinary)) {
        Write-Warning "Prepared tunnelmole runtime path '$sourceBinary' does not exist."
        Write-Warning "Continuing without bundling a standalone tunnelmole binary into the Windows runtime."
        return
    }

    Copy-Item $sourceBinary (Join-Path $RuntimeRoot ([System.IO.Path]::GetFileName($sourceBinary))) -Force
    Write-Host "Tunnelmole runtime bundled from $sourceBinary"
}

function Assert-BackendRuntimePins {
    param(
        [string]$PythonExe,
        [hashtable]$PinnedPackages
    )

    $expectedJson = ($PinnedPackages | ConvertTo-Json -Compress)
    $verificationRoot = Join-Path $artifactRoot "verification"
    New-Item -ItemType Directory -Force -Path $verificationRoot | Out-Null

    $jsonPath = Join-Path $verificationRoot "backend-runtime-pins.json"
    $scriptPath = Join-Path $verificationRoot "verify_backend_runtime_pins.py"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    [System.IO.File]::WriteAllText($jsonPath, $expectedJson, $utf8NoBom)

    $pythonCode = @'
import json
import sys
from importlib import metadata

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    expected = json.load(handle)

mismatches = []

for package_name, expected_version in expected.items():
    installed_version = metadata.version(package_name)
    if installed_version != expected_version:
        mismatches.append(f"{package_name} expected {expected_version} but found {installed_version}")

if mismatches:
    raise SystemExit("; ".join(mismatches))

print("Pinned backend runtime verified:", ", ".join(f"{name}=={version}" for name, version in sorted(expected.items())))
'@

    [System.IO.File]::WriteAllText($scriptPath, $pythonCode, $utf8NoBom)
    Invoke-CheckedCommand -FilePath $PythonExe -Arguments @($scriptPath, $jsonPath)
}

function Assert-PipDependencyConsistency {
    param(
        [string]$PythonExe
    )

    Write-Host "Checking installed Python dependency consistency..."
    Invoke-CheckedCommand -FilePath $PythonExe -Arguments @("-m", "pip", "check")
}

function Get-NormalizedPythonPackageName {
    param(
        [string]$Name
    )

    return $Name.Trim().ToLowerInvariant().Replace("_", "-")
}

function Get-InstalledPythonPackageVersion {
    param(
        [string]$PythonExe,
        [string]$PackageName
    )

    $pythonCode = @'
import sys
from importlib import metadata

try:
    print(metadata.version(sys.argv[1]))
except metadata.PackageNotFoundError:
    raise SystemExit(2)
'@

    $output = & $PythonExe -c $pythonCode $PackageName 2>$null
    if ($LASTEXITCODE -eq 0) {
        return (($output | Select-Object -First 1).Trim())
    }
    if ($LASTEXITCODE -eq 2) {
        return $null
    }

    throw "Failed to inspect installed Python package '$PackageName' with '$PythonExe'."
}

function Get-LockedConstraintSpec {
    param(
        [string]$ConstraintsFile,
        [string]$PackageName
    )

    if ([string]::IsNullOrWhiteSpace($ConstraintsFile) -or (-not (Test-Path $ConstraintsFile))) {
        return $null
    }

    $targetName = Get-NormalizedPythonPackageName -Name $PackageName
    foreach ($line in Get-Content $ConstraintsFile) {
        $trimmed = $line.Trim()
        if ($trimmed -notmatch '^([A-Za-z0-9_.-]+)==([^\s;#]+)') {
            continue
        }

        $lockedName = Get-NormalizedPythonPackageName -Name $Matches[1]
        if ($lockedName -eq $targetName) {
            return "$($Matches[1])==$($Matches[2])"
        }
    }

    return $null
}

function Sync-InstalledLockedPackage {
    param(
        [string]$PythonExe,
        [string]$ConstraintsFile,
        [string]$PackageName
    )

    $installedVersion = Get-InstalledPythonPackageVersion -PythonExe $PythonExe -PackageName $PackageName
    if (-not $installedVersion) {
        return
    }

    $lockedSpec = Get-LockedConstraintSpec -ConstraintsFile $ConstraintsFile -PackageName $PackageName
    if (-not $lockedSpec) {
        return
    }

    $expectedVersion = ($lockedSpec -split '==', 2)[1]
    if ($installedVersion -eq $expectedVersion) {
        return
    }

    Write-Host "Aligning installed optional runtime package $PackageName from $installedVersion to $expectedVersion"
    Invoke-CheckedCommand -FilePath $PythonExe -Arguments @(
        "-m", "pip", "install",
        "--upgrade",
        $lockedSpec,
        "-c", $ConstraintsFile
    )
}

function Assert-InstalledOptionalRuntimeImports {
    param(
        [string]$PythonExe,
        [string[]]$PackageNames
    )

    $verificationRoot = Join-Path $artifactRoot "verification"
    New-Item -ItemType Directory -Force -Path $verificationRoot | Out-Null

    $scriptPath = Join-Path $verificationRoot "verify_optional_runtime_imports.py"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    $pythonCode = @'
import importlib
import sys
from importlib import metadata

failures = []
for package_name in sys.argv[1:]:
    module_name = package_name.replace("-", "_")
    try:
        metadata.version(package_name)
    except metadata.PackageNotFoundError:
        continue
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        failures.append(f"{package_name}: {exc!r}")

if failures:
    raise SystemExit("Installed optional runtime package imports failed: " + "; ".join(failures))
'@

    [System.IO.File]::WriteAllText($scriptPath, $pythonCode, $utf8NoBom)
    Invoke-CheckedCommand -FilePath $PythonExe -Arguments (@($scriptPath) + $PackageNames)
}

function Assert-InstalledRuntimePackageImports {
    param(
        [string]$PythonExe,
        [string[]]$PackageImports
    )

    $verificationRoot = Join-Path $artifactRoot "verification"
    New-Item -ItemType Directory -Force -Path $verificationRoot | Out-Null

    $scriptPath = Join-Path $verificationRoot "verify_required_runtime_imports.py"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)

    $pythonCode = @'
import importlib
import sys
from importlib import metadata

failures = []
for spec in sys.argv[1:]:
    package_name, _, module_name = spec.partition("=")
    module_name = module_name or package_name.replace("-", "_")
    try:
        metadata.version(package_name)
    except metadata.PackageNotFoundError:
        failures.append(f"{package_name}: distribution is missing")
        continue
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        failures.append(f"{package_name} ({module_name}): {exc!r}")

if failures:
    raise SystemExit("Required runtime package imports failed: " + "; ".join(failures))
'@

    [System.IO.File]::WriteAllText($scriptPath, $pythonCode, $utf8NoBom)
    Invoke-CheckedCommand -FilePath $PythonExe -Arguments (@($scriptPath) + $PackageImports)
}

function Assert-CompiledBackendHardening {
    param(
        [string]$PythonExe,
        [string]$BundleRoot,
        [string]$RepoRoot,
        [string[]]$AllowedSourceFiles = @()
    )

    $scriptPath = Join-Path $RepoRoot "scripts\verify_backend_hardening.py"
    if (-not (Test-Path $scriptPath)) {
        throw "Backend hardening verifier not found at $scriptPath"
    }

    $arguments = @(
        $scriptPath,
        "--bundle-root", $BundleRoot,
        "--repo-root", $RepoRoot,
        "--allow-source-dir", "runtime_stdlib",
        "--allow-source-dir", "runtime_site_packages",
        "--skip-dir", "node",
        "--skip-dir", "runtime/node",
        "--skip-dir", "runtime/playwright",
        "--skip-dir", "runtime/tunnelmole"
    )

    foreach ($relativePath in $AllowedSourceFiles) {
        if (-not [string]::IsNullOrWhiteSpace($relativePath)) {
            $arguments += @("--allow-source-file", [string]$relativePath)
        }
    }

    Invoke-CheckedCommand -FilePath $PythonExe -Arguments $arguments
}

function Assert-PackagedBackendServerImports {
    param(
        [string]$BackendExe
    )

    if (-not (Test-Path $BackendExe)) {
        throw "Packaged backend executable not found at '$BackendExe'."
    }

    Write-Host "Verifying packaged backend server imports..."
    $verifyArguments = @(
        "--verify-server-imports",
        "--verify-runtime-import", "keyring",
        "--verify-runtime-import", "keyring.backends.Windows",
        "--verify-runtime-import", "shared.keystore",
        # The DataChannel bridge's loopback/SSRF gate. If this ever stops being
        # packaged the compiled server loses its proxy target policy, so fail
        # the build rather than ship without it.
        "--verify-runtime-import", "shared.proxy_target_policy",
        "--verify-runtime-import", "shared.ollama_gateway",
        "--verify-runtime-import", "shared.odysseus_gateway",
        "--verify-runtime-import", "shared.process_lifecycle",
        "--verify-runtime-import", "shared.remote_desktop_input",
        "--verify-runtime-import", "shared.remote_desktop_settings",
        # The native desktop client establishes the legacy client-module path,
        # then imports audio_streams when Cloud Pair initializes.
        "--verify-runtime-import", "v2.runtime.client",
        # Windows ships PyAudioWPatch rather than the non-Windows PyAudio wheel.
        "--verify-runtime-import", "pyaudiowpatch",
        "--verify-runtime-import", "httpx",
        "--verify-runtime-import", "websockets.asyncio.client",
        "--verify-runtime-import", "telethon",
        "--verify-runtime-import", "cryptg",
        "--verify-runtime-import", "autoyou_agents.agent_builder_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.backup_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.notify_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.remote_desktop_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.skills_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.education_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.tasks_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.voice_training_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.website_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.page_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.ads_watching_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.donation_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.data_collector_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.fine_tuning_agent.website.backend.app",
        "--verify-runtime-import", "autoyou_agents.data_collector_agent.agent",
        "--verify-runtime-import", "autoyou_agents.fine_tuning_agent.agent"
    )
    if ($Requirements -ne "base") {
        $verifyArguments += @(
            "--verify-runtime-import", "mss",
            "--verify-runtime-import", "pyautogui",
            # Every non-base profile carries requirements/local-llm.txt; without
            # its runtime the intent router silently defers every request.
            "--verify-runtime-import", "onnxruntime",
            "--verify-runtime-import", "shared.intent_router"
        )
    }
    if ($requirementsIncludesVoice) {
        $verifyArguments += @(
            "--verify-runtime-import", "shared.emotivoice_tts",
            "--verify-runtime-import", "RealtimeSTT",
            "--verify-runtime-import", "faster_whisper",
            "--verify-runtime-import", "ctranslate2",
            "--verify-runtime-import", "modelscope",
            "--verify-runtime-import", "nltk",
            "--verify-runtime-import", "numba",
            "--verify-runtime-import", "g2p_en",
            "--verify-runtime-import", "jieba",
            "--verify-runtime-import", "pypinyin",
            "--verify-runtime-import", "pypinyin_dict",
            "--verify-runtime-import", "cn2an",
            "--verify-runtime-import", "yacs"
        )
    }
    Invoke-CheckedCommand -FilePath $BackendExe -Arguments $verifyArguments -Environment @{
        "PYTHONDONTWRITEBYTECODE" = "1"
    }
    Write-Host "Packaged backend server imports verified"
}

function Copy-DirectoryContents {
    param(
        [string]$Source,
        [string]$Destination
    )

    if (-not (Test-Path $Source)) {
        throw "Copy source directory '$Source' does not exist."
    }

    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Copy-Item (Join-Path $Source "*") $Destination -Recurse -Force
}

function Remove-PatternsIfPresent {
    param(
        [string]$Root,
        [string[]]$Patterns
    )

    foreach ($pattern in $Patterns) {
        Remove-Item (Join-Path $Root $pattern) -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Remove-DirectoryNameRecursively {
    param(
        [string]$Root,
        [string]$DirectoryName
    )

    Get-ChildItem $Root -Directory -Recurse -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq $DirectoryName } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
}

function Remove-DirectoryPatternsRecursively {
    param(
        [string]$Root,
        [string[]]$Patterns
    )

    foreach ($pattern in $Patterns) {
        Get-ChildItem $Root -Directory -Recurse -Filter $pattern -Force -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Remove-FilePatternsRecursively {
    param(
        [string]$Root,
        [string[]]$Patterns,
        [string[]]$ExcludePaths = @()
    )

    foreach ($pattern in $Patterns) {
        Get-ChildItem $Root -File -Recurse -Filter $pattern -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notin $ExcludePaths } |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

Invoke-CheckedCommand -FilePath $pythonExe -Arguments @(
    $packagedGuidesScript,
    "--repo-root", $repoRoot,
    "--output-root", $runtimeGuidesRoot
)

Write-Host "Installing backend build dependencies..."
Invoke-CheckedCommand -FilePath $pythonExe -Arguments @(
    "-m", "pip", "install",
    "--upgrade",
    "pip>=26.1.2,<27",
    "setuptools>=83,<84",
    "wheel",
    "nuitka",
    "ordered-set",
    "zstandard"
)

Write-Host "Ensuring AutoYou runtime dependencies are installed in the build environment..."
Write-Host "Using backend requirements profile '$Requirements': $backendRuntimeRequirementsFile"
# Constrain packages present in the shared lock. This does not enforce its
# hashes or cover every optional/build dependency; audit the resolved artifact. Set
# AUTOYOU_IGNORE_LOCKFILE=1 to opt out.
$pipInstallArgs = @("-m", "pip", "install", "-r", $backendRuntimeRequirementsFile)
$ignoreLock = "$env:AUTOYOU_IGNORE_LOCKFILE".Trim().ToLower()
if ($ignoreLock -notin @("1", "true", "yes", "on")) {
    $lockedConstraints = (& $pythonExe (Join-Path $repoRoot "scripts\gen_locked_constraints.py")).Trim()
    if ($lockedConstraints -and (Test-Path $lockedConstraints)) {
        Write-Host "Pinning backend install to audited lockfile versions via $lockedConstraints"
        $pipInstallArgs += @("-c", $lockedConstraints)
    }
}
if ($requirementsIncludesVoice) {
    if (-not (Test-Path $realtimeSttRuntimeRequirementsFile)) {
        throw "Voice requirements requested, but '$realtimeSttRuntimeRequirementsFile' was not found."
    }
    Write-Host "Installing AutoYou RealtimeSTT runtime without wake-word extras..."
    Invoke-CheckedCommand -FilePath $pythonExe -Arguments @($realtimeSttRuntimeInstallerScript)
}
Invoke-CheckedCommand -FilePath $pythonExe -Arguments $pipInstallArgs
if ($includeCogneeRuntime) {
    if (-not (Test-Path $cogneeRequirementsFile)) {
        throw "AUTOYOU_INCLUDE_COGNEE requested, but '$cogneeRequirementsFile' was not found."
    }

    Write-Host "Installing optional Cognee memory backend from $cogneeRequirementsFile"
    $cogneePipInstallArgs = @("-m", "pip", "install", "-r", $cogneeRequirementsFile)
    if ($lockedConstraints -and (Test-Path $lockedConstraints)) {
        $cogneePipInstallArgs += @("-c", $lockedConstraints)
    }
    Invoke-CheckedCommand -FilePath $pythonExe -Arguments $cogneePipInstallArgs
}
if ($lockedConstraints -and (Test-Path $lockedConstraints)) {
    # Reused build environments can contain optional packages that are outside
    # the active profile. If present, keep them coherent with the audited lock
    # because Nuitka and runtime_site_packages still see installed packages.
    Sync-InstalledLockedPackage -PythonExe $pythonExe -ConstraintsFile $lockedConstraints -PackageName "transformers"
}
$reconcileArguments = @($runtimeDependencyReconcileScript)
if ($lockedConstraints -and (Test-Path $lockedConstraints)) {
    $reconcileArguments += @("--constraints", $lockedConstraints)
}
if ($requirementsIncludesTuning) {
    $reconcileArguments += "--include-tuning"
}
Invoke-CheckedCommand -FilePath $pythonExe -Arguments $reconcileArguments
Assert-PipDependencyConsistency -PythonExe $pythonExe
Assert-InstalledOptionalRuntimeImports -PythonExe $pythonExe -PackageNames @("huggingface-hub", "transformers")
if ($requirementsIncludesVoice) {
    Assert-InstalledRuntimePackageImports -PythonExe $pythonExe -PackageImports @(
        "PyAudioWPatch=pyaudiowpatch",
        "RealtimeSTT=RealtimeSTT",
        "faster-whisper=faster_whisper",
        "ctranslate2",
        "modelscope",
        "nltk",
        "numba",
        "g2p-en=g2p_en",
        "jieba",
        "pypinyin",
        "pypinyin-dict=pypinyin_dict",
        "cn2an",
        "yacs"
    )
}
if ($requirementsIncludesTuning) {
    Assert-InstalledOptionalRuntimeImports -PythonExe $pythonExe -PackageNames @("torch", "torchvision", "transformers", "datasets", "peft", "accelerate", "safetensors", "sentencepiece")
}
Write-Host "Resolved backend runtime pins: $(Format-BackendRuntimePins -PinnedPackages $backendRuntimePins -PackageNames $backendRuntimePinPackages)"
Assert-BackendRuntimePins -PythonExe $pythonExe -PinnedPackages $backendRuntimePins
Write-Host "Pruning non-commercial model assets from backend build environment..."
Invoke-CheckedCommand -FilePath $pythonExe -Arguments @(
    $nonCommercialAssetPrunerScript,
    "--prune",
    "--root", $sitePackagesRoot
)

Write-Host "Preparing bundled Node.js runtime..."
Install-PortableNodeRuntime -DestinationRoot $nodeRuntimeRoot -CacheRoot $downloadRoot

Write-Host "Preparing bundled Playwright Chromium runtime..."
New-Item -ItemType Directory -Force -Path $playwrightRoot | Out-Null
$existingChromium = Get-ChildItem $playwrightRoot -Filter "chromium-*" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existingChromium) {
    Write-Host "Chromium already present at $($existingChromium.FullName), skipping download."
}
else {
    # Check if Chromium is cached in the user's ms-playwright directory
    $msPlaywrightRoot = Join-Path $env:LOCALAPPDATA "ms-playwright"
    $cachedChromium = Get-ChildItem $msPlaywrightRoot -Filter "chromium-*" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cachedChromium) {
        Write-Host "Found cached Chromium at $($cachedChromium.FullName), copying to artifacts..."
        Copy-Item -Path $cachedChromium.FullName -Destination $playwrightRoot -Recurse -Force
        Write-Host "Chromium copied successfully."
    }
    else {
        Write-Host "No cached Chromium found, downloading from Playwright CDN..."
        Invoke-CheckedCommand -FilePath $pythonExe -Arguments @("-m", "playwright", "install", "chromium") -Environment @{
            "PLAYWRIGHT_BROWSERS_PATH" = $playwrightRoot
        }
    }
}

Write-Host "Installing Node.js dependencies for bundled services..."
$nodeServicesRoot = Join-Path $repoRoot "node"
foreach ($svc in @("whatsapp", "tunnelmole")) {
    $svcDir = Join-Path $nodeServicesRoot $svc
    if (-not (Test-Path (Join-Path $svcDir "package.json"))) {
        Write-Warning "No package.json found for node service '$svc' at $svcDir - skipping npm install."
        continue
    }
    $nmDir = Join-Path $svcDir "node_modules"
    Write-Host "Installing locked Node.js dependencies for '$svc'..."
    $bundledNodeCmd = Join-Path $nodeRuntimeRoot "node.exe"
    $npmScript = Join-Path $nodeRuntimeRoot "node_modules\npm\bin\npm-cli.js"
    if ((Test-Path $bundledNodeCmd) -and (Test-Path $npmScript)) {
        $pathWithNode = "$nodeRuntimeRoot;$env:PATH"
        Invoke-CheckedCommand -FilePath $bundledNodeCmd -Arguments @($npmScript, "ci", "--omit=dev", "--prefix", $svcDir) -Environment @{ "npm_config_cache" = (Join-Path $downloadRoot "npm-cache"); "PATH" = $pathWithNode }
    } else {
        # Fall back to system npm (PS 5.1-compatible: no ?. operator)
        $npmCmdObj = Get-Command "npm" -ErrorAction SilentlyContinue
        $npmCmd = if ($npmCmdObj) { $npmCmdObj.Source } else { $null }
        if (-not $npmCmd) {
            throw "npm not found. Ensure Node.js (with npm) is on PATH or build with bundled Node first."
        }
        Push-Location $svcDir
        try {
            Invoke-CheckedCommand -FilePath $npmCmd -Arguments @("ci", "--omit=dev")
        } finally {
            Pop-Location
        }
    }
    Write-Host "Locked npm dependencies installed for '$svc'."
}

Invoke-CheckedCommand -FilePath $pythonExe -Arguments @((Join-Path $repoRoot "scripts/prepare_intent_router.py"))
Write-Host "Compiling the AutoYou backend launcher with Nuitka (jobs=$JobCount)..."
$nuitkaBaseArguments = @(
    "-m", "nuitka",
    "--standalone"
)

$nuitkaCommonArguments = @(
    "--jobs=$JobCount",
    "--lto=no",
    "--low-memory",
    "--assume-yes-for-downloads",
    # Keep compiled file references runtime-relative instead of embedding checkout paths.
    "--file-reference-choice=runtime",
    # Preserve docstrings: upstream runtime libraries like SQLAlchemy and NumPy
    # still inspect their own docs during packaged bootstrap.
    "--windows-console-mode=disable",
    # dill-compat: required for multiprocessing with dill serialization
    "--enable-plugin=dill-compat",
    # multiprocessing: patches spawn so TranscriptionWorker (RealtimeSTT) can be
    # imported from the compiled standalone binary in the subprocess.
    "--enable-plugin=multiprocessing",
    # Disable anti-bloat to work around cv2 crash during binary analysis
    "--disable-plugin=anti-bloat",
    "--company-name=OpenStorey LLC",
    "--product-name=AutoYou",
    "--file-description=AutoYou Server Backend",
    "--file-version=$backendFileVersion",
    "--product-version=$backendFileVersion",
    "--copyright=Copyright (c) 2026 OpenStorey LLC. All rights reserved.",
    # Compile only the launcher stub here. The backend app modules are built
    # separately into Backend/runtime_modules as compiled extension modules.
    "--nofollow-imports",
    "--noinclude-pytest-mode=nofollow",
    "--output-dir=$nuitkaOutputRoot",
    "--output-filename=AutoYou.exe",
    "--nofollow-import-to=tests",
    # pystray loads its Win32 backend (_win32) via dynamic importlib call.
    "--include-package=pystray",
    "--include-package-data=pystray",
    # PIL is imported at launcher startup before runtime_modules/runtime_site_packages
    # are configured, so keep it inside the compiled stub as well.
    "--include-package=PIL",
    "--include-package-data=PIL",
    "--include-data-dir=$repoRoot\assets=assets",
    "--include-data-dir=$runtimeGuidesRoot=guides",
    "--include-data-dir=$repoRoot\node=node",
    "--include-data-file=$repoRoot\config\donations.example.json=config/donations.json",
    "--include-data-file=$repoRoot\VERSION=VERSION",
    "--include-data-dir=$repoRoot\requirements=requirements"
) + ($volatileNodeDataPatterns | ForEach-Object {
        "--noinclude-data-files=$_"
    }) + ($mutableRuntimeDataPatterns | ForEach-Object {
        "--noinclude-data-files=$_"
    }) + @(
    "$repoRoot\autoyou_app.py"
)

if ($NuitkaDiagnostics) {
    $nuitkaCommonArguments = @(
        "--show-progress",
        "--verbose"
    ) + $nuitkaCommonArguments
}

$compilerConfiguration = Get-NuitkaCompilerConfiguration -Compiler $BackendCompiler -PythonVersion $buildPythonVersion
$selectedCompiler = $compilerConfiguration.Compiler
$nuitkaArguments = $nuitkaBaseArguments + $compilerConfiguration.Arguments + $nuitkaCommonArguments

try {
    $effectiveJobCount = Invoke-NuitkaBuildWithRetry `
        -PythonExe $pythonExe `
        -Arguments $nuitkaArguments `
        -Environment $compilerConfiguration.Environment `
        -RequestedJobCount $JobCount `
        -OutputRoot $nuitkaOutputRoot
}
catch {
    $capturedOutput = Get-CapturedCommandOutput -ErrorRecord $_
    $shouldFallbackToMsvc = ($selectedCompiler -eq "clang") -and (Test-ClangFrontendCrash -CommandOutput $capturedOutput)
    $shouldFallbackToClang = ($selectedCompiler -eq "msvc") -and (
        (Test-MsvcHeapFailure -CommandOutput $capturedOutput) -or
        (Test-MsvcToolchainFailure -CommandOutput $capturedOutput)
    )

    if ((-not $shouldFallbackToMsvc) -and (-not $shouldFallbackToClang)) {
        throw
    }

    if ($shouldFallbackToMsvc) {
        Write-Warning "Visual Studio LLVM/Clang crashed while compiling generated C code. Retrying the backend build with MSVC."
        $fallbackCompiler = "msvc"
    }
    else {
        Write-Warning "MSVC failed while compiling generated C code or its toolchain became unavailable. Retrying the backend build with Visual Studio LLVM/Clang."
        $fallbackCompiler = "clang"
    }

    Remove-Item $nuitkaOutputRoot -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $nuitkaOutputRoot | Out-Null

    $compilerConfiguration = Get-NuitkaCompilerConfiguration -Compiler $fallbackCompiler -PythonVersion $buildPythonVersion
    $selectedCompiler = $compilerConfiguration.Compiler
    $nuitkaArguments = $nuitkaBaseArguments + $compilerConfiguration.Arguments + $nuitkaCommonArguments

    $effectiveJobCount = Invoke-NuitkaBuildWithRetry `
        -PythonExe $pythonExe `
        -Arguments $nuitkaArguments `
        -Environment $compilerConfiguration.Environment `
        -RequestedJobCount $JobCount `
        -OutputRoot $nuitkaOutputRoot
}

Write-Host "Nuitka build completed with --jobs=$effectiveJobCount using $selectedCompiler."

$compiledDist = Get-ChildItem $nuitkaOutputRoot -Directory |
Where-Object { $_.Name -like "*.dist" } |
Sort-Object LastWriteTime -Descending |
Select-Object -First 1

if (-not $compiledDist) {
    throw "Nuitka finished without producing a *.dist directory."
}

Remove-Item $finalBackendRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $finalBackendRoot | Out-Null
Copy-Item (Join-Path $compiledDist.FullName "*") $finalBackendRoot -Recurse -Force

$runtimeModulesRoot = Join-Path $finalBackendRoot "runtime_modules"
$runtimeStdlibRoot = Join-Path $finalBackendRoot "runtime_stdlib"
$runtimeSitePackagesRoot = Join-Path $finalBackendRoot "runtime_site_packages"
New-Item -ItemType Directory -Force -Path $runtimeStdlibRoot, $runtimeSitePackagesRoot | Out-Null

$runtimeModulesBuildRoot = Join-Path $backendArtifactRoot "runtime-modules-build"
Remove-Item $runtimeModulesBuildRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $runtimeModulesBuildRoot | Out-Null

$runtimeModuleBuildArguments = @(
    $runtimeModuleBuilderScript,
    "--repo-root", $repoRoot,
    "--bundle-root", $finalBackendRoot,
    "--build-root", $runtimeModulesBuildRoot,
    "--jobs", "$effectiveJobCount"
)
foreach ($argument in $compilerConfiguration.Arguments) {
    $runtimeModuleBuildArguments += @("--nuitka-arg=$argument")
}
$runtimeModuleBuildArguments += @("--nuitka-arg=--disable-plugin=transformers")
# Modules from another source tree are named by whoever runs the build, in the
# manifest AUTOYOU_EXTRA_SOURCES_MANIFEST points at; this script never looks for them.
$extraSourcesManifest = [string]$env:AUTOYOU_EXTRA_SOURCES_MANIFEST
if ($extraSourcesManifest) {
    if (-not (Test-Path -LiteralPath $extraSourcesManifest -PathType Leaf)) {
        throw "AUTOYOU_EXTRA_SOURCES_MANIFEST names a file that does not exist: $extraSourcesManifest"
    }
    $runtimeModuleBuildArguments += @("--extra-sources", $extraSourcesManifest)
} elseif ($DesktopV2) {
    throw "A desktop backend compiles the desktop app's own modules. Set AUTOYOU_EXTRA_SOURCES_MANIFEST to the manifest that lists them."
}
if ($requirementsIncludesVoice) {
    $runtimeModuleBuildArguments += "--include-emotivoice"
}
if ($NuitkaDiagnostics) {
    foreach ($argument in @("--show-progress", "--verbose")) {
        $runtimeModuleBuildArguments += @("--nuitka-arg=$argument")
    }
}

Write-Host "Compiling packaged runtime modules into $runtimeModulesRoot ..."
Invoke-CheckedCommand -FilePath $pythonExe -Arguments $runtimeModuleBuildArguments -Environment $compilerConfiguration.Environment

$requiredRuntimeModulePatterns = @(
    (Join-Path $runtimeModulesRoot "server*.pyd"),
    (Join-Path $runtimeModulesRoot "shared\\platform_runtime*.pyd"),
    (Join-Path $runtimeModulesRoot "shared\\client_conversation_contract*.pyd"),
    (Join-Path $runtimeModulesRoot "shared\\remote_desktop_input*.pyd"),
    (Join-Path $runtimeModulesRoot "shared\\remote_desktop_settings*.pyd"),
    (Join-Path $runtimeModulesRoot "autoyou_agents\\__init__.pyc")
)
if ($requirementsIncludesVoice) {
    $requiredRuntimeModulePatterns += @(
        (Join-Path $runtimeModulesRoot "vendor\\emotivoice\\models\\prompt_tts_modified\\jets*.pyd"),
        (Join-Path $runtimeModulesRoot "vendor\\emotivoice\\config\\joint\\config*.pyd")
    )
    foreach ($relativeAsset in @(
        "vendor\\emotivoice\\config\\joint\\config.yaml",
        "vendor\\emotivoice\\data\\youdao\\text\\tokenlist",
        "vendor\\emotivoice\\data\\youdao\\text\\speaker2",
        "vendor\\emotivoice\\lexicon\\librispeech-lexicon.txt",
        "vendor\\emotivoice\\LICENSE"
    )) {
        $requiredRuntimeModulePatterns += Join-Path $runtimeModulesRoot $relativeAsset
    }
}
foreach ($pattern in $requiredRuntimeModulePatterns) {
    if (-not (Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Select-Object -First 1)) {
        throw "Expected packaged runtime module matching '$pattern'."
    }
}

foreach ($assetName in @("admin-ui.js", "admin-ui.css", "logo.ico")) {
    $sourceAsset = Join-Path $repoRoot "assets\$assetName"
    $packagedAsset = Join-Path $finalBackendRoot "assets\$assetName"
    if ((-not (Test-Path $packagedAsset)) -or
        ((Get-Sha256Hex $sourceAsset) -ne (Get-Sha256Hex $packagedAsset))) {
        throw "Packaged admin asset '$packagedAsset' is missing or stale."
    }
}

# The intent router refuses model data that differs from its manifest by a
# single byte, so a missing or line-ending-converted file disables it silently.
$intentRouterManifest = Get-Content -Raw -LiteralPath (Join-Path $repoRoot "assets\intent_router\manifest.json") | ConvertFrom-Json
foreach ($entry in $intentRouterManifest.files.PSObject.Properties) {
    $packagedRouterFile = Join-Path $finalBackendRoot "assets\intent_router\$($entry.Name)"
    if ((-not (Test-Path -LiteralPath $packagedRouterFile)) -or
        ((Get-Item -LiteralPath $packagedRouterFile).Length -ne [int64]$entry.Value.bytes) -or
        ((Get-Sha256Hex $packagedRouterFile) -ne $entry.Value.sha256)) {
        throw "Packaged intent router file '$packagedRouterFile' is missing or does not match assets\intent_router\manifest.json."
    }
}

Copy-DirectoryContents -Source $stdlibRoot -Destination $runtimeStdlibRoot
Copy-DirectoryContents -Source $pythonDllsRoot -Destination (Join-Path $runtimeStdlibRoot "DLLs")
Copy-DirectoryContents -Source $sitePackagesRoot -Destination $runtimeSitePackagesRoot
$activeRuntimeRequirementsFiles = @($backendRuntimeRequirementsFile)
if ($requirementsIncludesVoice) {
    $activeRuntimeRequirementsFiles += $realtimeSttRuntimeRequirementsFile
}
if ($includeCogneeRuntime) {
    $activeRuntimeRequirementsFiles += $cogneeRequirementsFile
}
$runtimePruneArguments = @(
    $runtimeSitePackagesPrunerScript,
    "--site-packages-root", $runtimeSitePackagesRoot
)
foreach ($requirementsFile in $activeRuntimeRequirementsFiles) {
    $runtimePruneArguments += @("--requirements-file", $requirementsFile)
}
Invoke-CheckedCommand -FilePath $pythonExe -Arguments $runtimePruneArguments

Remove-PatternsIfPresent -Root $runtimeStdlibRoot -Patterns @(
    "site-packages",
    "test",
    "tkinter\test",
    "idlelib",
    "turtledemo",
    "ensurepip"
)
Remove-DirectoryNameRecursively -Root $runtimeStdlibRoot -DirectoryName "__pycache__"
Remove-FilePatternsRecursively -Root $runtimeStdlibRoot -Patterns @("*.pyc", "*.pyo", "*.pth")

Remove-PatternsIfPresent -Root $runtimeSitePackagesRoot -Patterns @(
    "pip",
    "pip-*.dist-info",
    "setuptools",
    "setuptools-*.dist-info",
    "wheel",
    "wheel-*.dist-info",
    "nuitka",
    "Nuitka-*.dist-info",
    "ordered_set",
    "ordered_set-*.dist-info",
    "zstandard",
    "zstandard-*.dist-info"
)
Remove-DirectoryNameRecursively -Root $runtimeSitePackagesRoot -DirectoryName "__pycache__"
Remove-DirectoryPatternsRecursively -Root $runtimeSitePackagesRoot -Patterns @("~*")
Remove-FilePatternsRecursively -Root $runtimeSitePackagesRoot -Patterns @("*.pyc", "*.pyo", "*.pth", "*.egg-link", "direct_url.json")
Write-Host "Pruning non-commercial model assets from packaged backend..."
Invoke-CheckedCommand -FilePath $pythonExe -Arguments @(
    $nonCommercialAssetPrunerScript,
    "--prune",
    "--root", $runtimeSitePackagesRoot,
    "--root", $finalBackendRoot
)

Assert-AdkBrowserBundle -BackendRoot $finalBackendRoot
Vendor-AdkBrowserFonts -BackendRoot $finalBackendRoot -CacheRoot $downloadRoot
Assert-AdkBrowserBundle -BackendRoot $finalBackendRoot

Copy-VoiceProcessingDlls -BackendRoot $finalBackendRoot -PythonExe $pythonExe -RequireFastTranscription:$requirementsIncludesVoice

$runtimeRoot = Join-Path $finalBackendRoot "runtime"
$runtimeNodeRoot = Join-Path $runtimeRoot "node"
$runtimePlaywrightRoot = Join-Path $runtimeRoot "playwright"
$runtimeWhisperRoot = Join-Path $runtimeRoot "whisper"
$runtimeTunnelmoleRoot = Join-Path $runtimeRoot "tunnelmole"

New-Item -ItemType Directory -Force -Path $runtimeRoot, $runtimeNodeRoot, $runtimePlaywrightRoot, $runtimeWhisperRoot, $runtimeTunnelmoleRoot | Out-Null
Copy-Item (Join-Path $nodeRuntimeRoot "*") $runtimeNodeRoot -Recurse -Force
Copy-Item (Join-Path $playwrightRoot "*") $runtimePlaywrightRoot -Recurse -Force
Install-WhisperCppRuntime -RuntimeRoot $runtimeWhisperRoot -PythonExe $pythonExe
Install-TunnelmoleRuntime -RuntimeRoot $runtimeTunnelmoleRoot -PythonExe $pythonExe

# ── Verify bundled Node.js is reachable ──────────────────────────────────────
$bundledNodeExe = Join-Path $runtimeNodeRoot "node.exe"
if (-not (Test-Path $bundledNodeExe)) {
    Write-Warning "Bundled node.exe NOT found at expected path: $bundledNodeExe"
    Write-Warning "Node-dependent services (WhatsApp) will fall back to system PATH."
    Write-Warning "Ensure Install-PortableNodeRuntime placed node.exe directly inside $nodeRuntimeRoot"
    # List what's actually there so the developer can diagnose:
    Write-Host "Contents of $runtimeNodeRoot :"
    Get-ChildItem $runtimeNodeRoot -ErrorAction SilentlyContinue | Select-Object Name, Length | Format-Table
} else {
    $nodeVersion = & $bundledNodeExe --version 2>&1
    Write-Host "Bundled Node.js verified: $bundledNodeExe  ($nodeVersion)"
}

# ── Verify node service node_modules are present ─────────────────────────────
# WhatsApp requires its own node_modules.
# These are bundled via --include-data-dir=node= but NPM native addons
# (.node files) must be compiled for the target Windows architecture.
# If node_modules are missing the services will fail on first launch.
$nodeServices = @("whatsapp", "tunnelmole")
$nodeBundledRoot = Join-Path $finalBackendRoot "node"
foreach ($svc in $nodeServices) {
    $svcDir = Join-Path $nodeBundledRoot $svc
    $nmDir  = Join-Path $svcDir "node_modules"
    if (-not (Test-Path $nmDir)) {
        Write-Warning "node_modules missing for service '$svc' at $svcDir"
        Write-Warning "Run 'npm install' inside $svcDir and rebuild, or the $svc service will not start."
    } else {
        Write-Host "node_modules present for service '$svc'"
    }
}
$requiredWhatsAppFiles = @("whatsapp_client.js", "wwebjs_recovery.js")
foreach ($fileName in $requiredWhatsAppFiles) {
    $filePath = Join-Path $nodeBundledRoot "whatsapp\$fileName"
    if (-not (Test-Path $filePath)) {
        throw "Packaged WhatsApp bridge is incomplete; missing '$filePath'."
    }
}

$runtimeIntegrityManifestPath = Join-Path $finalBackendRoot "runtime_integrity.json"
if (-not (Test-Path $runtimeIntegrityManifestPath)) {
    throw "Expected runtime integrity manifest at '$runtimeIntegrityManifestPath'."
}
$runtimeIntegrityManifest = Get-Content $runtimeIntegrityManifestPath -Raw | ConvertFrom-Json
$allowedRuntimeSourceFiles = @()
if ($runtimeIntegrityManifest.allowed_python_files) {
    $allowedRuntimeSourceFiles = @($runtimeIntegrityManifest.allowed_python_files | ForEach-Object { [string]$_ })
}

$agentPackageRoot = Join-Path $runtimeModulesRoot "autoyou_agents\notes_agent"
$agentManifest = Join-Path $agentPackageRoot "website\manifest.json"
if ((Test-Path $agentPackageRoot) -and (Test-Path $agentManifest)) {
    Write-Host "Agent package verified in compiled runtime bundle: $agentPackageRoot"
} else {
    Write-Warning "Could not verify autoyou_agents.notes_agent under $finalBackendRoot"
    Write-Warning "Sub-agents may not load at runtime. Verify runtime_modules/autoyou_agents assets were bundled correctly."
}

$donationFrontendRoot = Join-Path $runtimeModulesRoot "autoyou_agents\donation_agent\website\frontend"
$donationWebsiteRoot = Split-Path -Parent $donationFrontendRoot
foreach ($relativePath in @("index.html", "assets\app.js", "assets\styles.css")) {
    $donationAssetPath = Join-Path $donationFrontendRoot $relativePath
    if (-not (Test-Path $donationAssetPath)) {
        throw "Donation Agent frontend asset is missing from the compiled runtime bundle: '$donationAssetPath'."
    }
}
$donationManifestPath = Join-Path $donationWebsiteRoot "manifest.json"
if (-not (Test-Path $donationManifestPath)) {
    throw "Donation Agent website manifest is missing from the compiled runtime bundle: '$donationManifestPath'."
}
$donationConfigPath = Join-Path $finalBackendRoot "config\donations.json"
if (-not (Test-Path $donationConfigPath)) {
    throw "Donation configuration is missing from the compiled backend bundle: '$donationConfigPath'."
}

$dataCollectorFrontendRoot = Join-Path $runtimeModulesRoot "autoyou_agents\data_collector_agent\website\frontend"
foreach ($relativePath in @("index.html", "assets\app.js", "assets\styles.css")) {
    $dataCollectorAssetPath = Join-Path $dataCollectorFrontendRoot $relativePath
    if (-not (Test-Path $dataCollectorAssetPath)) {
        throw "Data Collector Agent frontend asset is missing from the compiled runtime bundle: '$dataCollectorAssetPath'."
    }
}
if (-not (Test-Path (Join-Path $runtimeModulesRoot "autoyou_agents\data_collector_agent\website\manifest.json"))) {
    throw "Data Collector Agent website manifest is missing from the compiled runtime bundle."
}
if (-not (Test-Path (Join-Path $runtimeModulesRoot "autoyou_agents\data_collector_agent\whatsapp_history_dump.mjs"))) {
    throw "Data Collector Agent WhatsApp worker is missing from the compiled runtime bundle."
}

$fineTuningFrontendRoot = Join-Path $runtimeModulesRoot "autoyou_agents\fine_tuning_agent\website\frontend"
foreach ($relativePath in @("index.html", "app.js", "styles.css")) {
    $fineTuningAssetPath = Join-Path $fineTuningFrontendRoot $relativePath
    if (-not (Test-Path $fineTuningAssetPath)) {
        throw "Fine Tuning Agent frontend asset is missing from the compiled runtime bundle: '$fineTuningAssetPath'."
    }
}
if (-not (Test-Path (Join-Path $runtimeModulesRoot "autoyou_agents\fine_tuning_agent\website\manifest.json"))) {
    throw "Fine Tuning Agent website manifest is missing from the compiled runtime bundle."
}

$locationFrontendRoot = Join-Path $runtimeModulesRoot "autoyou_agents\location_agent\website\frontend"
foreach ($relativePath in @("index.html", "assets\app.js", "assets\styles.css")) {
    $locationAssetPath = Join-Path $locationFrontendRoot $relativePath
    if (-not (Test-Path $locationAssetPath)) {
        throw "Location Agent frontend asset is missing from the compiled runtime bundle: '$locationAssetPath'."
    }
}
if (-not (Test-Path (Join-Path (Split-Path -Parent $locationFrontendRoot) "manifest.json"))) {
    throw "Location Agent website manifest is missing from the compiled runtime bundle."
}

foreach ($directoryName in @("uploads", "output", "memory", "signal_data", "logs")) {
    New-Item -ItemType Directory -Force -Path (Join-Path $finalBackendRoot $directoryName) | Out-Null
}

$legalBundleTarget = Join-Path $finalBackendRoot "Legal"
$legalScriptPath = Join-Path $repoRoot "scripts\copy_release_legal_artifacts.py"
if ((Test-Path $defaultPythonExe) -and (Test-Path $legalScriptPath)) {
    $legalProfile = Resolve-BackendLegalArtifactId -Requirements $Requirements
    Write-Host "Generating legal bundle '$legalProfile' for backend artifact at $legalBundleTarget..."
    & $defaultPythonExe $legalScriptPath --artifact $legalProfile --target $legalBundleTarget --generate | Out-Null
}

Assert-PackagedBackendServerImports -BackendExe (Join-Path $finalBackendRoot "AutoYou.exe")
Remove-DirectoryNameRecursively -Root $runtimeModulesRoot -DirectoryName "__pycache__"
$runtimeManifestTrackedBytecode = @(
    $runtimeIntegrityManifest.files.psobject.Properties |
        Where-Object { $_.Name -like "runtime_modules/*.pyc" -or $_.Name -like "runtime_modules/*.pyo" } |
        ForEach-Object { Join-Path $finalBackendRoot ($_.Name -replace '/', '\') }
)
Remove-FilePatternsRecursively -Root $runtimeModulesRoot -Patterns @("*.pyc", "*.pyo") -ExcludePaths $runtimeManifestTrackedBytecode
Assert-CompiledBackendHardening -PythonExe $pythonExe -BundleRoot $finalBackendRoot -RepoRoot $repoRoot -AllowedSourceFiles $allowedRuntimeSourceFiles

$backendLegalArtifactId = Resolve-BackendLegalArtifactId -Requirements $Requirements
Invoke-CheckedCommand -FilePath $pythonExe -Arguments @(
    $legalCopyScript,
    "--artifact", $backendLegalArtifactId,
    "--target", (Join-Path $finalBackendRoot "Legal"),
    "--generate"
)

Write-Host "Backend bundle ready at $finalBackendRoot"
