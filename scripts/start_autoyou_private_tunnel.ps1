[CmdletBinding()]
param(
    [string]$TunnelClient,
    [string]$TunnelId = $env:CONTROL_PLANE_TUNNEL_ID,
    [string]$McpServerUrl = "http://127.0.0.1:8071/mcp"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$launcher = Join-Path $repoRoot "scripts\start_autoyou_private_tunnel.ps1"
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Use the AutoYou workspace launcher at $launcher. The tunnel runtime key must be supplied separately as CONTROL_PLANE_API_KEY."
}

& $launcher -TunnelClient $TunnelClient -TunnelId $TunnelId -McpServerUrl $McpServerUrl
exit $LASTEXITCODE
