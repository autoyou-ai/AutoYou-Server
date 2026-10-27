# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AppRoot,
    [string]$UserDataRoot,
    [string]$RuntimeRoot,
    [switch]$SkipDownload,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
if (-not [string]::IsNullOrWhiteSpace($env:AUTOYOU_TEST_ROOT)) {
    $UserDataRoot = $env:AUTOYOU_TEST_ROOT
}
if ([string]::IsNullOrWhiteSpace($UserDataRoot)) {
    $UserDataRoot = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'AutoYou'
}
$UserDataRoot = [System.IO.Path]::GetFullPath($UserDataRoot)
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = Join-Path $AppRoot 'Backend\runtime\ffmpeg'
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$reportPath = Join-Path $UserDataRoot 'ffmpeg-setup.json'

function Save-FFmpegSetupReport($Report) {
    if (-not $CheckOnly) {
        New-Item -ItemType Directory -Force -Path $UserDataRoot | Out-Null
        $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
        [System.IO.File]::WriteAllText($reportPath, ($Report | ConvertTo-Json -Depth 6), $encoding)
    }
    $Report | ConvertTo-Json -Depth 6 -Compress | Write-Output
}

function Resolve-FFmpegExecutable {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($env:AUTOYOU_TV_FFMPEG)) {
        $candidates.Add($env:AUTOYOU_TV_FFMPEG)
    }
    $cmd = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        $candidates.Add($cmd.Source)
    }
    $candidates.Add((Join-Path $RuntimeRoot 'bin\ffmpeg.exe'))
    $candidates.Add((Join-Path $RuntimeRoot 'ffmpeg.exe'))
    $candidates.Add((Join-Path $AppRoot 'Backend\runtime\ffmpeg\bin\ffmpeg.exe'))
    $candidates.Add((Join-Path $AppRoot 'runtime\ffmpeg\bin\ffmpeg.exe'))

    $localApp = $env:LOCALAPPDATA
    if ($localApp) {
        $wingetPackages = Join-Path $localApp 'Microsoft\WinGet\Packages'
        if (Test-Path $wingetPackages) {
            $found = Get-ChildItem -Path $wingetPackages -Recurse -Filter 'ffmpeg.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($found) { $candidates.Add($found.FullName) }
        }
        $candidates.Add((Join-Path $localApp 'Microsoft\WinGet\Links\ffmpeg.exe'))
    }
    $candidates.Add('C:\ffmpeg\bin\ffmpeg.exe')
    $candidates.Add('C:\Program Files\ffmpeg\bin\ffmpeg.exe')

    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate) -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            $versionOutput = @(& $candidate -version 2>$null)
            if ($LASTEXITCODE -eq 0 -and $versionOutput) {
                return [System.IO.Path]::GetFullPath($candidate)
            }
        }
        catch { continue }
    }
    return $null
}

$resolvedFFmpeg = Resolve-FFmpegExecutable

if ($resolvedFFmpeg) {
    $ffprobe = Join-Path (Split-Path -Parent $resolvedFFmpeg) 'ffprobe.exe'
    if (-not (Test-Path $ffprobe)) { $ffprobe = $null }

    $report = [ordered]@{
        status = 'ready'
        configured = $true
        ffmpeg_path = $resolvedFFmpeg
        ffprobe_path = $ffprobe
        runtime_root = (Split-Path -Parent $resolvedFFmpeg)
        updated_at = [DateTime]::UtcNow.ToString('o')
    }
    Save-FFmpegSetupReport $report
    exit 0
}

if ($CheckOnly -or $SkipDownload) {
    $report = [ordered]@{
        status = 'missing'
        configured = $false
        error = 'FFmpeg executable not found'
        updated_at = [DateTime]::UtcNow.ToString('o')
    }
    Save-FFmpegSetupReport $report
    exit 0
}

# Attempt winget installation first
$wingetCmd = Get-Command winget.exe -ErrorAction SilentlyContinue
if ($wingetCmd) {
    try {
        Write-Host "Installing FFmpeg via winget..."
        & winget.exe install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements --silent 2>$null
        $resolvedFFmpeg = Resolve-FFmpegExecutable
        if ($resolvedFFmpeg) {
            $ffprobe = Join-Path (Split-Path -Parent $resolvedFFmpeg) 'ffprobe.exe'
            $report = [ordered]@{
                status = 'ready'
                configured = $true
                installed_via = 'winget'
                ffmpeg_path = $resolvedFFmpeg
                ffprobe_path = $ffprobe
                runtime_root = (Split-Path -Parent $resolvedFFmpeg)
                updated_at = [DateTime]::UtcNow.ToString('o')
            }
            Save-FFmpegSetupReport $report
            exit 0
        }
    }
    catch {
        Write-Warning "Winget installation attempt failed: $_"
    }
}

# Portable zip download fallback
try {
    Write-Host "Downloading portable FFmpeg essentials build..."
    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
    $zipPath = Join-Path $RuntimeRoot 'ffmpeg-release-essentials.zip'
    $url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    
    [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12 -bor [System.Net.SecurityProtocolType]::Tls13
    $webClient = New-Object System.Net.WebClient
    $webClient.DownloadFile($url, $zipPath)
    
    Write-Host "Extracting portable FFmpeg..."
    $extractTemp = Join-Path $RuntimeRoot 'temp_extract'
    Expand-Archive -Path $zipPath -DestinationPath $extractTemp -Force
    
    $binDir = Get-ChildItem -Path $extractTemp -Recurse -Directory -Filter "bin" | Select-Object -First 1
    if ($binDir) {
        $destBin = Join-Path $RuntimeRoot 'bin'
        New-Item -ItemType Directory -Force -Path $destBin | Out-Null
        Copy-Item (Join-Path $binDir.FullName "*") $destBin -Force
        Remove-Item $extractTemp -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $zipPath -Force -ErrorAction SilentlyContinue
        
        $resolvedFFmpeg = Join-Path $destBin 'ffmpeg.exe'
        $ffprobe = Join-Path $destBin 'ffprobe.exe'
        $report = [ordered]@{
            status = 'ready'
            configured = $true
            installed_via = 'portable_download'
            ffmpeg_path = $resolvedFFmpeg
            ffprobe_path = $ffprobe
            runtime_root = $destBin
            updated_at = [DateTime]::UtcNow.ToString('o')
        }
        Save-FFmpegSetupReport $report
        exit 0
    }
}
catch {
    $report = [ordered]@{
        status = 'failed'
        configured = $false
        error = $_.Exception.Message
        updated_at = [DateTime]::UtcNow.ToString('o')
    }
    Save-FFmpegSetupReport $report
    exit 1
}
