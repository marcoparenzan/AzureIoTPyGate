<#
.SYNOPSIS
Creates a stable, private paho-mqtt 2.1.0 site. Never installs a global tool/package.
.DESCRIPTION
Destination: -PythonSite, then PYGATE_PYTHON_SITE, then the legacy stable site.
Use -CopyFrom for an offline copy; otherwise pysharp install runs in a fresh staging
working directory (its CLI creates ./site-packages). Existing valid sites are kept.
No device config is changed. Use -WhatIf to preview without creating any directory.
Copyright (c) 2026 Marco Parenzan. MIT License.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$PythonSite,
    [string]$CopyFrom
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
function Assert-Paho([string]$Site) {
    if (-not (Test-Path -LiteralPath (Join-Path $Site 'paho/mqtt/client.py') -PathType Leaf)) { throw 'Missing client module.' }
    $metadata = Join-Path $Site 'paho_mqtt-2.1.0.dist-info/METADATA'
    if (-not (Test-Path -LiteralPath $metadata -PathType Leaf)) { throw 'Missing pinned distribution metadata.' }
    $text = [IO.File]::ReadAllText($metadata)
    if ($text -cnotmatch '(?m)^Name: paho-mqtt\r?$' -or $text -cnotmatch '(?m)^Version: 2\.1\.0\r?$') { throw 'Unexpected package version.' }
    # Fail rather than merge two versions in the same import site.
    $versions = @(Get-ChildItem -LiteralPath $Site -Directory -Filter 'paho*dist-info')
    if ($versions.Count -ne 1) { throw 'Ambiguous distribution versions.' }
    foreach ($item in @(Get-Item -LiteralPath $Site -Force) + @(Get-ChildItem -LiteralPath $Site -Recurse -Force)) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse points are unsupported.' }
    }
}
try {
    if (-not $PythonSite) {
        $PythonSite = if ($env:PYGATE_PYTHON_SITE) { $env:PYGATE_PYTHON_SITE } else { 'D:\Configurations\AzureIoTForge\python-site' } # legacy default
    }
    $target = [IO.Path]::GetFullPath($PythonSite)
    if (Test-Path -LiteralPath $target) {
        Assert-Paho $target
        Write-Output 'KEPT: existing stable paho-mqtt==2.1.0 site; no installation or config change.'
        exit 0
    }
    if ($CopyFrom) {
        $source = (Get-Item -LiteralPath $CopyFrom).FullName
        Assert-Paho $source
    } elseif (-not (Get-Command pysharp -ErrorAction SilentlyContinue)) { throw 'PySharp is not on PATH.' }
    $parent = Split-Path $target -Parent
    # Scratch stays outside the repository, in the mandated artifact root.
    $stage = Join-Path 'D:\Dev\BuildArtifacts\AzureIoTPyGate\ops' ('paho-staging-' + [guid]::NewGuid().ToString('N'))
    $action = if ($CopyFrom) { 'Copy pinned paho into a new private stable site' } else { 'Install paho-mqtt==2.1.0 into a new private stable site via PySharp' }
    if (-not $PSCmdlet.ShouldProcess('private paho site', $action)) { exit 0 }
    $null = New-Item -ItemType Directory -Path $parent -Force
    $null = New-Item -ItemType Directory -Path $stage
    $site = Join-Path $stage 'site-packages'
    if ($CopyFrom) {
        $null = New-Item -ItemType Directory -Path $site
        Get-ChildItem -LiteralPath $source -Force | Copy-Item -Destination $site -Recurse -Force
    } else {
        Push-Location $stage
        try {
            # Help for installed PySharp preview.10 confirms no --target option:
            # install always writes ./site-packages, so the working directory is essential.
            # Suppress raw CLI output; it may contain private index URLs or local paths.
            $null = & pysharp install 'paho-mqtt==2.1.0' 2>&1
            if ($LASTEXITCODE -ne 0) { throw 'Private package install failed.' }
        } finally { Pop-Location }
    }
    Assert-Paho $site
    # New-Item without Force refuses an existing target, including a racing installer.
    # Copy supports a stable site on a different volume. A failed partial NEW site
    # is retained for inspection and will fail validation on the next invocation.
    $null = New-Item -ItemType Directory -Path $target
    Get-ChildItem -LiteralPath $site -Force | Copy-Item -Destination $target -Recurse
    Assert-Paho $target
    Write-Output $(if ($CopyFrom) { 'COPIED: pinned paho-mqtt==2.1.0 into the private stable site.' } else { 'INSTALLED: pinned paho-mqtt==2.1.0 into the private stable site using PySharp.' })
    Write-Output 'No global installation; no device config changes. Set pahoSite in separately managed configs and PYGATE_PAHO_SITE for tests.'
    Write-Output 'The staging directory was retained. Move it to the BuildArtifacts trash after inspection; do not delete artifacts outright.'
} catch {
    Write-Output 'BLOCKED: private site validation/install failed. No existing site was replaced. Inspect any retained staging directory locally; raw installer output is intentionally suppressed.'
    exit 1
}
