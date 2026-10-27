# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$AppRoot,
    [string]$UserDataRoot,
    [string]$RuntimeRoot,
    [string]$NodeExecutable,
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$PackageVersion = '1.2.0',
    [switch]$SkipDownload,
    [switch]$SkipAgentRegistration,
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
    $RuntimeRoot = Join-Path $UserDataRoot 'tools\pi'
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$reportPath = Join-Path $UserDataRoot 'pi-setup.json'

function Save-PiSetupReport($Report) {
    if (-not $CheckOnly) {
        New-Item -ItemType Directory -Force -Path $UserDataRoot | Out-Null
        $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
        [System.IO.File]::WriteAllText($reportPath, ($Report | ConvertTo-Json -Depth 6), $encoding)
    }
    $Report | ConvertTo-Json -Depth 6 -Compress | Write-Output
}

function Resolve-PiNode {
    $pathNode = Get-Command node.exe -ErrorAction SilentlyContinue
    $candidates = @(
        $NodeExecutable,
        $env:AUTOYOU_NODE_EXE,
        (Join-Path $AppRoot 'Backend\runtime\node\node.exe'),
        $(if ($pathNode) { $pathNode.Source })
    )
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate) -or -not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        try {
            $versionOutput = @(& $candidate --version 2>$null)
            $version = [string]($versionOutput | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and [version]$version.Trim().TrimStart('v') -ge [version]'22.19.0') {
                return [System.IO.Path]::GetFullPath($candidate)
            }
        }
        catch { continue }
    }
    return $null
}

function Find-PiCli {
    $roots = @($env:PI_PATH, $RuntimeRoot,
        (Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Projects\Pi'),
        (Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'npm'))
    if (-not [string]::IsNullOrWhiteSpace($env:AUTOYOU_TEST_ROOT)) { $roots = @($RuntimeRoot) }
    foreach ($candidateRoot in $roots) {
        if ([string]::IsNullOrWhiteSpace($candidateRoot)) { continue }
        foreach ($relative in @(
            'packages\coding-agent\dist\bundle\cli.js',
            'packages\coding-agent\dist\cli.js',
            'node_modules\@earendil-works\pi-coding-agent\dist\bundle\cli.js',
            'dist\bundle\cli.js'
        )) {
            $candidate = Join-Path $candidateRoot $relative
            if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
            try {
                $versionOutput = @(& $script:PiNode $candidate --offline --version 2>$null)
                $versionText = [string]($versionOutput | Select-Object -First 1)
                if ($LASTEXITCODE -eq 0 -and $versionText -match '(\d+\.\d+\.\d+)' -and [version]$Matches[1] -ge [version]'1.2.0') {
                    $script:ResolvedPiRoot = [System.IO.Path]::GetFullPath($candidateRoot)
                    return [System.IO.Path]::GetFullPath($candidate)
                }
            }
            catch { Write-Verbose ('Pi CLI version probe failed: ' + $_.Exception.Message); continue }
        }
    }
    return $null
}

function Install-PiPackage {
    $npmCli = Join-Path (Split-Path -Parent $script:PiNode) 'node_modules\npm\bin\npm-cli.js'
    if (-not (Test-Path -LiteralPath $npmCli -PathType Leaf)) {
        throw 'The selected Node runtime does not include npm. Install the official Node.js LTS runtime and retry.'
    }
    New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null
    $stdout = Join-Path $RuntimeRoot 'setup.stdout.log'
    $stderr = Join-Path $RuntimeRoot 'setup.stderr.log'
    # Node runs npm directly: no shell parsing, global installs, or lifecycle scripts.
    $arguments = '"' + $npmCli + '" --prefix "' + $RuntimeRoot.TrimEnd('\') +
        '" install --ignore-scripts --no-audit --no-fund --save-exact @earendil-works/pi-coding-agent@' + $PackageVersion
    $process = Start-Process -FilePath $script:PiNode -ArgumentList $arguments -WindowStyle Hidden `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    if (-not $process.WaitForExit(180000)) {
        & (Join-Path $env:WINDIR 'System32\taskkill.exe') /PID $process.Id /T /F 2>$null | Out-Null
        throw 'Pi installation timed out. Review the Pi setup logs and retry when the package registry is reachable.'
    }
    if ($process.ExitCode -ne 0) {
        throw 'Pi package installation failed. Review tools\pi\setup.stderr.log and retry.'
    }
}

function Enable-PiAgents {
    param([bool]$IncludePi = $false)
    $registryPath = Join-Path $UserDataRoot 'agent_install_registry.json'
    $registry = [pscustomobject]@{ schema_version = 1; agents = [pscustomobject]@{} }
    if (Test-Path -LiteralPath $registryPath) {
        try { $registry = Get-Content -LiteralPath $registryPath -Raw -Encoding UTF8 | ConvertFrom-Json }
        catch { throw 'The existing agent registry is encrypted or unreadable. Enable Pi and Coding Agent in AutoYou Agent Management.' }
    }
    if ($null -eq $registry -or $registry -isnot [pscustomobject]) { throw 'The existing agent registry is not a JSON object.' }
    if (-not $registry.agents) { $registry | Add-Member -NotePropertyName agents -NotePropertyValue ([pscustomobject]@{}) -Force }
    if ($registry.agents -isnot [pscustomobject]) { throw 'The existing agent entries are not a JSON object.' }
    $timestamp = [DateTime]::UtcNow.ToString('o')
    $agentNames = @('coding_agent')
    if ($IncludePi) { $agentNames += 'pi_agent' }
    foreach ($name in $agentNames) {
        $registry.agents | Add-Member -NotePropertyName $name -NotePropertyValue ([pscustomobject]@{
            installed = $true; source = 'windows-pi-setup'; updated_at = $timestamp
            description = 'Enabled by Windows Easy Setup. Pi is used when its runtime and model are ready.'
        }) -Force
    }
    $installed = @($registry.agents.PSObject.Properties | Where-Object { $_.Value.installed } | Select-Object -ExpandProperty Name | Sort-Object)
    $available = @($registry.agents.PSObject.Properties | Where-Object { -not $_.Value.installed } | Select-Object -ExpandProperty Name | Sort-Object)
    $registry | Add-Member -NotePropertyName installed_agents -NotePropertyValue $installed -Force
    $registry | Add-Member -NotePropertyName available_agents -NotePropertyValue $available -Force
    $registry | Add-Member -NotePropertyName updated_at -NotePropertyValue $timestamp -Force
    New-Item -ItemType Directory -Force -Path $UserDataRoot | Out-Null
    $encoding = New-Object System.Text.UTF8Encoding -ArgumentList $false
    $temporaryPath = $registryPath + '.pi-setup-' + [Guid]::NewGuid().ToString('N') + '.tmp'
    [System.IO.File]::WriteAllText($temporaryPath, ($registry | ConvertTo-Json -Depth 12), $encoding)
    if (Test-Path -LiteralPath $registryPath) {
        [System.IO.File]::Replace($temporaryPath, $registryPath, [NullString]::Value)
    }
    else { [System.IO.File]::Move($temporaryPath, $registryPath) }
}

try {
    # Keep the built-in coding fallback available even if this optional download fails.
    if (-not ($CheckOnly -or $SkipAgentRegistration)) { Enable-PiAgents }
    $script:PiNode = Resolve-PiNode
    if (-not $script:PiNode) { throw 'Pi requires Node.js 22.19 or newer. The desktop bundle normally includes this runtime.' }
    $cli = Find-PiCli
    if (-not $cli -and -not ($SkipDownload -or $CheckOnly)) {
        Install-PiPackage
        $cli = Find-PiCli
    }
    if (-not $cli) { throw 'A compatible Pi CLI was not found. Run Easy Setup with Internet access or select an installed Pi directory in AI & Models.' }
    if (-not ($CheckOnly -or $SkipAgentRegistration)) { Enable-PiAgents -IncludePi $true }
    Save-PiSetupReport ([ordered]@{
        state = 'ready'; cli = $cli; node = $script:PiNode; runtime_root = $script:ResolvedPiRoot
        details = 'Pi CLI is ready. Coding prefers the Pi subagent when the selected model is available; setup does not change the main AI provider.'
        checked_at_utc = [DateTime]::UtcNow.ToString('o')
    })
    exit 0
}
catch {
    Save-PiSetupReport ([ordered]@{ state = 'pending'; details = $_.Exception.Message; checked_at_utc = [DateTime]::UtcNow.ToString('o') })
    exit 1
}
