# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

# Resolve the packaged server path relative to this script by default.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DistPath = if ($env:AUTOYOU_WINDOWS_DIST_PATH) {
    $env:AUTOYOU_WINDOWS_DIST_PATH
} else {
    Join-Path $ScriptDir "dist\AutoYou-win-x64"
}

# 1. Environment Variables
$env:AUTOYOU_INSTANCE_NAME = "AutoYou"
$env:AUTOYOU_ADMIN_PORT = "8001"
$env:AUTOYOU_AI_PORT = "8081"
$env:AUTOYOU_AUTH_PORT = "8002"

# 2. Ensure logs directory exists (prevents Start-Process from failing)
$LogDir = Join-Path $DistPath "logs"
if (!(Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
    Write-Host "Created logs directory at $LogDir" -ForegroundColor Cyan
}

# 3. Cleanup existing instances to prevent port conflicts
Write-Host "Cleaning up existing AutoYou processes..." -ForegroundColor Cyan
Get-Process -Name "AutoYou" -ErrorAction SilentlyContinue | Stop-Process -Force

# 4. Start the Server
Write-Host "Starting AutoYou Server using the saved network access setting..." -ForegroundColor Green
$Process = Start-Process -FilePath "$DistPath\AutoYou.exe" `
    -ArgumentList "--run-server --admin 8001 --ai-agent 8081 --auth 8002" `
    -WorkingDirectory $DistPath `
    -RedirectStandardOutput "$LogDir\server_mode.log" `
    -RedirectStandardError "$LogDir\server_mode_error.log" `
    -PassThru

# 4. Tail the logs immediately
Write-Host "Monitoring logs (Press Ctrl+C to stop viewing logs, server will keep running)..." -ForegroundColor Yellow
Get-Content "$LogDir\server_mode.log" -Wait -Tail 20
