[CmdletBinding()]
param(
    [string]$TunnelClient,
    [string]$ProfileDirectory = "$env:USERPROFILE\.config\autoyou\tunnel-client-profiles",
    [string]$Profile = "autoyou-private"
)

$ErrorActionPreference = "Stop"

function Get-AutoYouDotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $entry = Get-Content -LiteralPath $Path | Where-Object {
        $_ -match "^\s*$([regex]::Escape($Name))\s*="
    } | Select-Object -Last 1
    if (-not $entry) {
        return $null
    }
    return (($entry -split "=", 2)[1]).Trim().Trim('"').Trim("'")
}

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$repoEnvPath = Join-Path $repoRoot ".env"
if (-not (Test-Path -LiteralPath $repoEnvPath)) {
    throw "Missing owner-private environment file: $repoEnvPath"
}

$storedOpenAIKey = Get-AutoYouDotEnvValue -Path $repoEnvPath -Name "OPENAI_API_KEY"
if ($storedOpenAIKey) {
    $env:OPENAI_API_KEY = $storedOpenAIKey
}
$storedOrganizationId = Get-AutoYouDotEnvValue -Path $repoEnvPath -Name "CONTROL_PLANE_ORGANIZATION_ID"
if ($storedOrganizationId) {
    $env:CONTROL_PLANE_ORGANIZATION_ID = $storedOrganizationId
}
if (-not $env:OPENAI_API_KEY) {
    throw "OPENAI_API_KEY is not configured in the process or owner-private .env file."
}
if (-not $env:CONTROL_PLANE_ORGANIZATION_ID) {
    throw "CONTROL_PLANE_ORGANIZATION_ID is not configured in the process or owner-private .env file."
}

if (-not $TunnelClient) {
    $command = Get-Command "tunnel-client" -ErrorAction SilentlyContinue
    if ($command) {
        $TunnelClient = $command.Source
    } else {
        $TunnelClient = Get-ChildItem -Path "$env:USERPROFILE\Downloads\tunnel-client-*-windows-amd64\tunnel-client.exe" -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
}
if (-not $TunnelClient -or -not (Test-Path -LiteralPath $TunnelClient -PathType Leaf)) {
    throw "tunnel-client.exe was not found. Pass -TunnelClient with its full path."
}
if (-not (Test-Path -LiteralPath $ProfileDirectory -PathType Container)) {
    throw "Tunnel profile directory was not found: $ProfileDirectory"
}

$env:HARPOON_TARGETS = "label=openai-control,url=https://api.openai.com,desc=OpenAI tunnel control plane"
$env:LOG_LEVEL = "info"
Remove-Item Env:HARPOON_CAPTURE_PAYLOADS -ErrorAction SilentlyContinue
Remove-Item Env:LOG_HTTP_RAW_UNSAFE -ErrorAction SilentlyContinue

& $TunnelClient run --profile-dir $ProfileDirectory --profile $Profile
exit $LASTEXITCODE
