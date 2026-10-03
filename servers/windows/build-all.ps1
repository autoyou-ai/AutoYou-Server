# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

[CmdletBinding()]
param(
    [ValidateSet("Debug", "Release")]
    [string]$Configuration = "Release",
    [switch]$Clean,
    [switch]$SkipBackend,
    [switch]$AcceptTerms,
    [ValidateSet("clang", "msvc", "zig")]
    [string]$BackendCompiler = "clang",
    [switch]$IncludeCognee,
    [switch]$AllowOpenReleaseBlockers,
    [switch]$SkipReleaseLegalGate,
    [ValidateSet("binary-default", "connector-full", "training-full")]
    [string]$ReleaseProfile = "binary-default",
    [string]$Version,
    [int]$JobCount = 0
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptRoot "..\..")).Path
$defaultPythonExe = Join-Path $repoRoot ".venv\Scripts\python.exe"

function Get-BackendRuntimeSpec {
    param(
        [string]$PythonExe,
        [string]$RepoRoot,
        [string]$ReleaseProfile
    )

    $scriptPath = Join-Path $RepoRoot "scripts\read_backend_runtime_pins.py"
    $requirementsFile = if ($ReleaseProfile -eq "training-full") {
        Join-Path $RepoRoot "requirements\training-full.txt"
    } elseif ($ReleaseProfile -eq "connector-full") {
        Join-Path $RepoRoot "requirements.txt"
    } else {
        Join-Path $RepoRoot "requirements\binary-default.txt"
    }
    if ((Test-Path $PythonExe) -and (Test-Path $scriptPath) -and (Test-Path $requirementsFile)) {
        $arguments = @(
            $scriptPath,
            "--requirements-file", $requirementsFile,
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

$backendRuntimeSpec = Get-BackendRuntimeSpec -PythonExe $defaultPythonExe -RepoRoot $repoRoot -ReleaseProfile $ReleaseProfile

Write-Host "Using release profile: $ReleaseProfile"
Write-Host "Using backend runtime spec: $backendRuntimeSpec"
if ($IncludeCognee -or ("$env:AUTOYOU_INCLUDE_COGNEE".Trim().ToLower() -in @("1", "true", "yes", "on"))) {
    Write-Host "Including optional Cognee memory backend in packaged runtime."
}
if ($SkipBackend) {
    Write-Host "Reusing existing packaged backend at $PSScriptRoot\artifacts\backend\AutoYouServer"
}

$publishArguments = @{
    Configuration = $Configuration
    Clean = [bool]$Clean
    SkipBackend = [bool]$SkipBackend
    AcceptTerms = [bool]$AcceptTerms
    BackendCompiler = $BackendCompiler
    IncludeCognee = [bool]$IncludeCognee
    AllowOpenReleaseBlockers = [bool]$AllowOpenReleaseBlockers
    SkipReleaseLegalGate = [bool]$SkipReleaseLegalGate
    ReleaseProfile = $ReleaseProfile
    JobCount = $JobCount
}
if (-not [string]::IsNullOrWhiteSpace($Version)) {
    $publishArguments["Version"] = $Version
}

& (Join-Path $PSScriptRoot "publish-desktop.ps1") @publishArguments
if (-not $?) {
    throw "Windows desktop publish failed."
}

$desktopRoot = Join-Path $PSScriptRoot "dist\AutoYou-win-x64"
$desktopHostExecutable = Join-Path $desktopRoot "AutoYou.exe"
$desktopHostAssembly = Join-Path $desktopRoot "AutoYou.dll"
$desktopBackendExecutable = Join-Path $desktopRoot "Backend\AutoYou.exe"
if ((Test-Path $desktopHostAssembly) -and (Test-Path $desktopHostExecutable) -and (Test-Path $desktopBackendExecutable)) {
    $desktopHostHash = (Get-FileHash -Algorithm SHA256 $desktopHostExecutable).Hash
    $desktopBackendHash = (Get-FileHash -Algorithm SHA256 $desktopBackendExecutable).Hash
    if ($desktopHostHash -eq $desktopBackendHash) {
        throw "The Windows distribution root contains the backend executable instead of the native tray host."
    }
    if (-not (Test-Path (Join-Path $desktopRoot "Assets\logo.ico"))) {
        throw "The Windows distribution is missing the required Assets\logo.ico host icon."
    }
}
