# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

<#
.SYNOPSIS
    Bring the local autoyou.me services back after a reboot.

.DESCRIPTION
    Cloudflared runs as a Windows service and Docker Desktop autostarts from the
    HKCU Run key, so the tunnel and the containers return on their own. The
    native processes do not: the support gateway and the mike backend both
    need the operator, which is why native endpoints could answer 502 after a
    restart until somebody runs the operator.

    This is the piece that notices. Register it with -Install and it runs at log
    on, waits for the Docker engine, and calls `autoyou_system.py up`.

    Log on rather than startup, deliberately: Docker Desktop is a per-user
    application, and the support gateway wants both the user's CUDA context and
    AUTOYOU_SUPPORT_KEY from the User environment. A boot with no log on leaves
    the containers up and these down - the same state as today, not worse.

.PARAMETER Install
    Register (or replace) the scheduled task, then exit.

.PARAMETER Uninstall
    Remove the scheduled task, then exit.

.PARAMETER DelaySeconds
    Wait before the first attempt, so Docker Desktop has a head start. The task
    registers with this as its own delay; running by hand it is applied inline.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\autoyou_autostart.ps1 -Install
#>

[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$Uninstall,
    [int]$DelaySeconds = 90,
    [int]$DockerWaitSeconds = 300,
    [int]$WaitSeconds = 420
)

$ErrorActionPreference = 'Stop'
$TaskName = 'AutoYou local services'
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Get-LogPath {
    $dir = $env:AUTOYOU_OPERATOR_STATE_DIR
    if (-not $dir) { $dir = Join-Path $env:LOCALAPPDATA 'AutoYou\operator' }
    $logs = Join-Path $dir 'logs'
    if (-not (Test-Path $logs)) { New-Item -ItemType Directory -Path $logs -Force | Out-Null }
    Join-Path $logs 'autostart.log'
}

function Write-Log {
    param(
        [string]$Message,
        # Under the scheduler the log file is the only reader there is: the task
        # runs with a hidden console, and writing an operator's whole output to
        # a stream nobody drains is a way to stall a run that has otherwise
        # finished its work. Only -Install and -Uninstall have someone watching.
        [switch]$Echo
    )
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    if ($Echo) { Write-Host $line }
    Add-Content -Path (Get-LogPath) -Value $line -Encoding utf8 -ErrorAction SilentlyContinue
}

function Get-PythonPath {
    # The venv interpreter is what the operator has been exercised with. `py -3`
    # is the fallback, and a missing interpreter is worth failing loudly on
    # rather than silently leaving the services down.
    $venv = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path $venv) { return $venv }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    throw "no Python interpreter found (looked for $venv and py)"
}

function Install-Task {
    $ps = (Get-Command powershell.exe).Source
    $script = Join-Path $PSScriptRoot 'autoyou_autostart.ps1'
    $arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $script

    $action = New-ScheduledTaskAction -Execute $ps -Argument $arguments -WorkingDirectory $RepoRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $trigger.Delay = 'PT{0}S' -f $DelaySeconds

    # A laptop on battery must still serve its own tunnel, and the run is long
    # enough that the default 72-hour kill is the only limit worth keeping.
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 5) `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -MultipleInstances IgnoreNew

    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force `
        -Description 'Starts the support gateway and mike backend after a reboot. See scripts/autoyou_autostart.ps1.' | Out-Null

    Write-Log -Echo "registered scheduled task '$TaskName' (at log on, +${DelaySeconds}s)"
}

function Uninstall-Task {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Log -Echo "removed scheduled task '$TaskName'"
    } else {
        Write-Log -Echo "no scheduled task named '$TaskName'"
    }
}

function Wait-ForDocker {
    param([int]$TimeoutSeconds)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        # Windows PowerShell 5.1 turns a native command's stderr into
        # ErrorRecords, which $ErrorActionPreference = 'Stop' then promotes to a
        # terminating error - so `docker info` on a cold engine would kill this
        # script rather than being the "not ready yet" it is. Exit code is the
        # only signal worth reading here.
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            & docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
            $code = $LASTEXITCODE
        } catch {
            $code = 1
        } finally {
            $ErrorActionPreference = $previous
        }
        if ($code -eq 0) { return $true }
        Start-Sleep -Seconds 5
    }
    return $false
}

function Invoke-Up {
    # AUTOYOU_SUPPORT_KEY lives at User scope and the gateway fails closed
    # without it. A task started by the logon trigger normally inherits it, but
    # a process launched from a shell that predates the variable does not - and
    # that failure looks like "attention required" with no reason attached.
    if (-not $env:AUTOYOU_SUPPORT_KEY) {
        $key = [Environment]::GetEnvironmentVariable('AUTOYOU_SUPPORT_KEY', 'User')
        if ($key) {
            $env:AUTOYOU_SUPPORT_KEY = $key
            Write-Log 'took AUTOYOU_SUPPORT_KEY from the User environment'
        } else {
            Write-Log 'WARNING AUTOYOU_SUPPORT_KEY is not set; the chat gateway will fail closed'
        }
    }

    $python = Get-PythonPath
    $operator = Join-Path $RepoRoot 'scripts\autoyou_system.py'

    # Same 5.1 trap as Wait-ForDocker, and worse here: the operator reports a
    # service needing attention on stderr, which is the case this script exists
    # to retry. Under 'Stop' that diagnostic would terminate the retry instead
    # of triggering it.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $python $operator up --wait-seconds $WaitSeconds 2>&1
        $code = $LASTEXITCODE
    } catch {
        $output = @("operator invocation failed: $($_.Exception.Message)")
        $code = 1
    } finally {
        $ErrorActionPreference = $previous
    }

    foreach ($line in $output) { Write-Log "  $line" }
    return $code
}

if ($Install) { Install-Task; return }
if ($Uninstall) { Uninstall-Task; return }

# One run at a time, enforced here rather than trusted to the scheduler.
#
# `MultipleInstances IgnoreNew` only covers runs the scheduler knows about, and
# it does not cover all of them: Stop-ScheduledTask returns the task to Ready
# while this process is still alive, and RestartCount can fire on top of a run
# that is merely slow. Two of these were observed running at once. Neither would
# bind a port twice - the operator's own state check adopts a live process - but
# two runs interleaving their writes to that state file is not worth finding out
# about the hard way.
$mutex = New-Object System.Threading.Mutex($false, 'Global\AutoYouLocalServicesAutostart')
$held = $false
try {
    try {
        $held = $mutex.WaitOne(0)
    } catch [System.Threading.AbandonedMutexException] {
        # A previous run died holding it. The mutex is ours now, and its
        # processes are exactly what `up` is about to reconcile.
        $held = $true
    }
    if (-not $held) {
        Write-Log 'another autostart run holds the lock; leaving it to finish'
        exit 0
    }

Write-Log '--- autostart run ---'

if (Wait-ForDocker -TimeoutSeconds $DockerWaitSeconds) {
    Write-Log 'docker engine is available'
} else {
    # Not fatal. `up` launches Docker Desktop itself, and the two process-only
    # services do not need the engine at all, so a slow engine should not stop
    # the broker from coming back.
    Write-Log "WARNING docker engine not ready after ${DockerWaitSeconds}s; continuing"
}

    $code = Invoke-Up
    if ($code -ne 0) {
        # A slow first attempt is the common case, not a broken one: the support
        # gateway loads a model before it binds, so the operator's health check
        # can time out on a service that is simply still starting. The retry
        # costs a minute and `up` adopts anything already running.
        Write-Log "first attempt reported exit $code; retrying once in 60s"
        Start-Sleep -Seconds 60
        $code = Invoke-Up
    }
    Write-Log "autostart finished with exit $code"
    exit $code
}
finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
