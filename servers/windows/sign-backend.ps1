# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AutoYou Windows Authenticode signing.
#
# The Nuitka backend build (build-backend.ps1) intentionally does NO code
# signing ("external Authenticode is the operator's responsibility"). This
# script is that step: it Authenticode-signs the packaged backend's PE files
# (the launcher .exe, and optionally the compiled .pyd / .dll modules) so
# Windows SmartScreen / Defender recognise a legit, signed build from an
# authorized publisher.
#
# Prerequisites (operator-supplied - cannot be bundled):
#   - An OV/EV code-signing certificate from a public CA, Azure Trusted Signing,
#     or another certificate chain trusted by Microsoft for public distribution.
#   - The cert installed in the Windows cert store (use -CertThumbprint) OR a PFX
#     file (use -PfxPath/-PfxPassword), OR Azure Artifact Signing client tools
#     (use -ArtifactSigningDlibPath/-ArtifactSigningMetadataPath).
#   - Windows SDK signtool.exe on PATH or under Program Files.
#
# Usage:
#   pwsh servers/windows/sign-backend.ps1 -BundleRoot servers/windows/artifacts/backend/AutoYouServer `
#        -CertThumbprint ABCD... -TimestampUrl http://timestamp.digicert.com
#   pwsh servers/windows/sign-backend.ps1 -PfxPath cert.pfx -PfxPassword $env:PFX_PW -IncludeModules
#   pwsh servers/windows/sign-backend.ps1 -ArtifactSigningDlibPath Azure.CodeSigning.Dlib.dll `
#        -ArtifactSigningMetadataPath metadata.json -TimestampUrl http://timestamp.acs.microsoft.com
#   pwsh servers/windows/sign-backend.ps1 -PreflightOnly -CertThumbprint ABCD...
#
# Environment fallbacks: AUTOYOU_WIN_CERT_THUMBPRINT, AUTOYOU_WIN_PFX_PATH,
# AUTOYOU_WIN_PFX_PASSWORD, AUTOYOU_WIN_ARTIFACT_SIGNING_DLIB,
# AUTOYOU_WIN_ARTIFACT_SIGNING_METADATA, AUTOYOU_WIN_TIMESTAMP_URL.

[CmdletBinding()]
param(
    [string]$BundleRoot = "servers/windows/artifacts/backend/AutoYouServer",
    [string[]]$FilePath,
    [string]$CertThumbprint = $env:AUTOYOU_WIN_CERT_THUMBPRINT,
    [string]$PfxPath = $env:AUTOYOU_WIN_PFX_PATH,
    [string]$PfxPassword = $env:AUTOYOU_WIN_PFX_PASSWORD,
    [string]$ArtifactSigningDlibPath = $env:AUTOYOU_WIN_ARTIFACT_SIGNING_DLIB,
    [string]$ArtifactSigningMetadataPath = $env:AUTOYOU_WIN_ARTIFACT_SIGNING_METADATA,
    [string]$TimestampUrl = $(if ($env:AUTOYOU_WIN_TIMESTAMP_URL) { $env:AUTOYOU_WIN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }),
    [string]$Description = "AutoYou",
    [switch]$IncludeModules,
    [switch]$SkipReleaseLegalGate,
    [switch]$SkipLegalBundleCheck,
    [switch]$VerifyOnly,
    [switch]$DryRun,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "[sign] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[ok]   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[warn] $msg" -ForegroundColor Yellow }

function Assert-PathExists {
    param(
        [string]$Path,
        [string]$Description
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing ${Description}: $Path"
    }
}

function Get-RepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

function Invoke-StrictReleaseLegalGate {
    $repoRoot = Get-RepoRoot
    $gateScript = Join-Path $repoRoot "scripts\check_release_legal_gates.py"
    Assert-PathExists -Path $gateScript -Description "release legal gate script"

    $python = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { $null }
    if (-not $python) {
        throw "Could not find python. Install Python before Authenticode signing."
    }

    Write-Step "Running strict release legal gate..."
    & $python $gateScript --artifact-scope server --no-generate --strict-unknown-license
    if ($LASTEXITCODE -ne 0) {
        throw "Release legal gate failed. Resolve open blockers before Windows Authenticode signing."
    }
}

function Assert-ReleaseLegalBundle {
    param([string]$Root)
    $legalRoot = Join-Path $Root "Legal"
    foreach ($fileName in @("LICENSE", "NOTICE.txt", "sbom.cdx.json", "THIRD-PARTY-NOTICES.md")) {
        Assert-PathExists -Path (Join-Path $legalRoot $fileName) -Description "backend legal bundle $fileName"
    }
    Write-Ok "Release legal bundle verified"
}

function Resolve-SignTool {
    $cmd = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "${env:ProgramFiles}\Windows Kits\10\bin"
    ) | Where-Object { $_ -and (Test-Path $_) }
    foreach ($root in $roots) {
        $found = Get-ChildItem -Path $root -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
                 Where-Object { $_.FullName -match "x64" } |
                 Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    throw "signtool.exe not found. Install the Windows SDK or add signtool to PATH."
}

function Assert-SigningCredential {
    if ($VerifyOnly) { return "verify-only" }
    if ($CertThumbprint) {
        $normalizedThumbprint = ($CertThumbprint -replace "\s", "").ToUpperInvariant()
        $cert = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -CodeSigningCert -ErrorAction SilentlyContinue |
            Where-Object { ($_.Thumbprint -replace "\s", "").ToUpperInvariant() -eq $normalizedThumbprint -and $_.HasPrivateKey } |
            Select-Object -First 1
        if (-not $cert) {
            throw "Code-signing certificate with private key not found in CurrentUser/My or LocalMachine/My: $CertThumbprint"
        }
        return "store thumbprint"
    }
    if ($PfxPath) {
        Assert-PathExists -Path $PfxPath -Description "PFX file"
        return "PFX $PfxPath"
    }
    if ($ArtifactSigningDlibPath -and $ArtifactSigningMetadataPath) {
        Assert-PathExists -Path $ArtifactSigningDlibPath -Description "Azure Artifact Signing dlib"
        Assert-PathExists -Path $ArtifactSigningMetadataPath -Description "Azure Artifact Signing metadata"
        return "Azure Artifact Signing dlib"
    }
    throw "Provide -CertThumbprint (store), -PfxPath (+ -PfxPassword), or -ArtifactSigningDlibPath with -ArtifactSigningMetadataPath."
}

function Get-PesToSign {
    param([string]$root, [bool]$includeModules)
    if (-not (Test-Path $root)) { throw "BundleRoot not found: $root" }
    $patterns = @("*.exe")
    if ($includeModules) { $patterns += @("*.dll", "*.pyd") }
    # Skip vendored stdlib/site-packages PE files; sign AutoYou's own launcher +
    # (optionally) the compiled runtime_modules.
    $skip = @("runtime_stdlib", "runtime_site_packages", "node", "runtime\node", "runtime\playwright")
    Get-ChildItem -Path $root -Recurse -Include $patterns -File -ErrorAction SilentlyContinue |
        Where-Object {
            $rel = $_.FullName.Substring($root.Length)
            -not ($skip | Where-Object { $rel -match [regex]::Escape($_) })
        }
}

function Resolve-ExplicitPesToSign {
    param([string[]]$Paths)
    foreach ($path in $Paths) {
        Assert-PathExists -Path $path -Description "PE file"
        $item = Get-Item -LiteralPath $path
        if ($item.Extension.ToLowerInvariant() -notin @(".exe", ".dll", ".pyd", ".bin")) {
            throw "FilePath must point to .exe, .dll, .pyd, or Partner Center .bin files: $path"
        }
        $item
    }
}

function Build-SignArgs {
    param([string]$file)
    $args = @("sign", "/fd", "sha256", "/td", "sha256", "/tr", $TimestampUrl, "/d", $Description)
    if ($CertThumbprint) {
        $args += @("/sha1", $CertThumbprint)
    } elseif ($PfxPath) {
        Assert-PathExists -Path $PfxPath -Description "PFX file"
        $args += @("/f", $PfxPath)
        if ($PfxPassword) { $args += @("/p", $PfxPassword) }
    } elseif ($ArtifactSigningDlibPath -and $ArtifactSigningMetadataPath) {
        Assert-PathExists -Path $ArtifactSigningDlibPath -Description "Azure Artifact Signing dlib"
        Assert-PathExists -Path $ArtifactSigningMetadataPath -Description "Azure Artifact Signing metadata"
        $args += @("/dlib", $ArtifactSigningDlibPath, "/dmdf", $ArtifactSigningMetadataPath)
    } else {
        throw "Provide -CertThumbprint (store), -PfxPath (+ -PfxPassword), or -ArtifactSigningDlibPath with -ArtifactSigningMetadataPath."
    }
    $args += $file
    return $args
}

function Invoke-VerifyFile {
    param([string]$file)
    & $signtool verify /pa /q $file | Out-Null
    return ($LASTEXITCODE -eq 0)
}

$usingExplicitFiles = $FilePath -and $FilePath.Count -gt 0

$signtool = Resolve-SignTool
Write-Step "Using signtool: $signtool"

if ($PreflightOnly) {
    $credential = Assert-SigningCredential
    Write-Ok "Signing preflight passed ($credential)."
    exit 0
}

if (-not $DryRun -and -not $SkipReleaseLegalGate) {
    Invoke-StrictReleaseLegalGate
    if (-not $SkipLegalBundleCheck -and -not $usingExplicitFiles) {
        Assert-ReleaseLegalBundle -Root $BundleRoot
    }
} elseif ($SkipReleaseLegalGate) {
    Write-Warn "Skipping strict release legal gate."
}
if ($usingExplicitFiles) {
    Write-Step "Explicit file(s): $($FilePath.Count)"
} else {
    Write-Step "Bundle root:    $BundleRoot"
}
if (-not $VerifyOnly) {
    $credential = Assert-SigningCredential
    Write-Step "Timestamp URL:  $TimestampUrl"
    Write-Step "Credential:     $credential"
}

$files = if ($usingExplicitFiles) {
    @(Resolve-ExplicitPesToSign -Paths $FilePath)
} else {
    @(Get-PesToSign -root $BundleRoot -includeModules:$IncludeModules.IsPresent)
}
if (-not $files -or $files.Count -eq 0) {
    Write-Warn "No PE files matched under $BundleRoot."
    exit 0
}
Write-Step ("Will {0} {1} file(s){2}." -f $(if ($VerifyOnly) { "verify" } else { "sign" }), $files.Count, $(if ($IncludeModules) { " (incl. modules)" } else { "" }))

$signed = 0; $failed = 0
foreach ($f in $files) {
    if ($VerifyOnly) {
        if (Invoke-VerifyFile -file $f.FullName) {
            $signed++
        } else {
            Write-Warn "verify failed: $($f.FullName)"
            $failed++
        }
        continue
    }
    $signArgs = Build-SignArgs -file $f.FullName
    if ($DryRun) {
        Write-Host ("  DRYRUN signtool {0}" -f ($signArgs -join " "))
        continue
    }
    & $signtool @signArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Warn "sign failed: $($f.FullName)"; $failed++; continue }
    & $signtool verify /pa /q $f.FullName | Out-Null
    if ($LASTEXITCODE -ne 0) {
        if ($env:AUTOYOU_ALLOW_TEST_SIGNATURE -eq "1" -or $env:AUTOYOU_ALLOW_TEST_SIGNATURE -eq "true") {
            Write-Warn "verify warning (allowed for test signature): $($f.FullName)"
        } else {
            Write-Warn "verify failed: $($f.FullName)"
            $failed++
            continue
        }
    }
    $signed++
}

if ($DryRun -and -not $VerifyOnly) { Write-Ok "Dry run complete (no files modified)."; exit 0 }
if ($VerifyOnly) {
    Write-Ok ("Verified {0} file(s); {1} failure(s)." -f $signed, $failed)
} else {
    Write-Ok ("Signed + verified {0} file(s); {1} failure(s)." -f $signed, $failed)
}
if ($failed -gt 0) { exit 1 }
