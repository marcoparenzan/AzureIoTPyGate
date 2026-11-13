<#
.SYNOPSIS
Read-only readiness table: .NET 11 SDK, PySharp, private paho, configs and processes.
.DESCRIPTION
No login, Azure call, import of agent code, device start, file write or process stop.
An active PySharp process is a blocker (do not kill another session's process).
Copyright (c) 2026 Marco Parenzan. MIT License.
#>
[CmdletBinding()]
param(
    [string]$DevicesRoot,
    [string]$PythonSite,
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string[]]$DeviceId
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$rows = [Collections.Generic.List[object]]::new()
function Add-Check([string]$Check, [bool]$Pass, [string]$Detail) {
    $rows.Add([pscustomobject]@{ Check = $Check; Status = $(if ($Pass) { 'PASS' } else { 'BLOCK' }); Detail = $Detail })
}
function Field($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -ne $property) { return $property.Value }
    return $null
}
function Object-Json([string]$Path) {
    $value = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($value -isnot [pscustomobject]) { throw 'Expected object.' }
    return $value
}
function Config-Path([string]$Base, $Value) {
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value)) { throw 'Missing path.' }
    if ([IO.Path]::IsPathRooted($Value)) { return $Value }
    return [IO.Path]::GetFullPath((Join-Path $Base $Value))
}
function Check-Paho([string]$Site) {
    if (-not $Site -or -not (Test-Path -LiteralPath (Join-Path $Site 'paho/mqtt/client.py') -PathType Leaf)) { return $false }
    $metadata = Join-Path $Site 'paho_mqtt-2.1.0.dist-info/METADATA'
    if (-not (Test-Path -LiteralPath $metadata -PathType Leaf)) { return $false }
    $text = [IO.File]::ReadAllText($metadata)
    return ($text -cmatch '(?m)^Name: paho-mqtt\r?$' -and $text -cmatch '(?m)^Version: 2\.1\.0\r?$' -and @(Get-ChildItem -LiteralPath $Site -Directory -Filter 'paho*dist-info').Count -eq 1)
}

try {
    if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) { throw 'SDK not found.' }
    $sdks = @(& dotnet --list-sdks 2>$null)
    $ok = $LASTEXITCODE -eq 0 -and @($sdks | Where-Object { $_ -match '^11\.\d+\.\d+' }).Count -gt 0
    Add-Check '.NET 11 SDK' $ok 'Project targets only net11.0; SDK major version 11 inventory only.'
} catch { Add-Check '.NET 11 SDK' $false 'Cannot read SDK inventory.' }
try {
    if (-not (Get-Command pysharp -ErrorAction SilentlyContinue)) { throw 'Tool not found.' }
    $versionText = (& pysharp --version 2>$null | Out-String).Trim()
    $versionOk = $LASTEXITCODE -eq 0 -and $versionText -match '^2\.5\.1(?:\.0|-net11-preview\.10|-preview\.10)?$'
    # The real CLI reports assembly version 2.5.1.0, not the package preview suffix.
    # Verify the approved preview separately using read-only global-tool metadata.
    $toolList = (& dotnet tool list -g 2>$null | Out-String)
    $packageOk = $LASTEXITCODE -eq 0 -and $toolList -match '(?m)^pysharp\s+2\.5\.1-net11-preview\.10\s'
    Add-Check 'PySharp on PATH/version' ($versionOk -and $packageOk) 'CLI 2.5.1.0 and approved package 2.5.1-net11-preview.10; own runtime may be .NET 11.'
} catch { Add-Check 'PySharp on PATH/version' $false 'Tool missing or cannot execute version query.' }
if (-not $PythonSite) { $PythonSite = if ($env:PYGATE_PYTHON_SITE) { $env:PYGATE_PYTHON_SITE } else { 'D:\Configurations\AzureIoTForge\python-site' } } # legacy default
try { Add-Check 'Stable paho site' (Check-Paho $PythonSite) 'Private site must contain client.py and paho-mqtt 2.1.0 metadata.' }
catch { Add-Check 'Stable paho site' $false 'Cannot read private site.' }
if (-not $DevicesRoot) {
    $DevicesRoot = if ($env:PYGATE_DEVICES_ROOT) { $env:PYGATE_DEVICES_ROOT } elseif ($env:FORGE_CONFIG_ROOT) { Join-Path $env:FORGE_CONFIG_ROOT 'devices' } else { 'D:\Configurations\AzureIoTForge\devices' } # legacy fallbacks
}
try {
    if (-not (Test-Path -LiteralPath $DevicesRoot -PathType Container)) { throw 'Missing root.' }
    Add-Check 'Devices root' $true 'Readable external credential root (no contents printed).'
    if ($DeviceId) { $folders = @($DeviceId | ForEach-Object { Join-Path $DevicesRoot $_ }) }
    else { $folders = @(Get-ChildItem -LiteralPath $DevicesRoot -Directory | ForEach-Object { $_.FullName }) }
    if ($folders.Count -eq 0) { Add-Check 'Device configs' $false 'No device folders found; supply -DeviceId to select packages.' }
    $index = 0
    foreach ($folder in $folders) {
        $index++
        $label = 'Device package #' + $index
        try {
            $config = Object-Json (Join-Path $folder 'agent-config.json')
            $allow = Field $config 'allowDpsRegistration'
            if ($allow -isnot [bool] -or $allow -ne $false) { throw 'Explicit false required.' }
            foreach ($name in @('registrationId', 'idScope', 'globalEndpoint')) {
                $value = Field $config $name
                if ($value -isnot [string] -or [string]::IsNullOrWhiteSpace($value) -or $value.Contains('<')) { throw 'Missing config identity.' }
            }
            foreach ($name in @('cert', 'key', 'stateFile')) {
                if (-not (Test-Path -LiteralPath (Config-Path $folder (Field $config $name)) -PathType Leaf)) { throw 'Missing required file.' }
            }
            if (-not (Test-Path -LiteralPath (Config-Path $folder (Field $config 'payloadDir')) -PathType Container)) { throw 'Missing payload directory.' }
            # main.py inserts pahoSite as-is; do not imply a config-relative site works.
            $site = Field $config 'pahoSite'
            if ($site -isnot [string] -or -not [IO.Path]::IsPathRooted($site) -or -not (Check-Paho $site)) { throw 'Missing absolute per-config paho site.' }
            $state = Object-Json (Config-Path $folder (Field $config 'stateFile'))
            $hub = Field $state 'assignedHub'
            if ($hub -isnot [string] -or [string]::IsNullOrWhiteSpace($hub) -or (Field $state 'deviceId') -cne (Field $config 'registrationId')) { throw 'Missing cached assignment.' }
            $family = Field $config 'identityFamily'
            if ($null -eq $family) { $family = 'X509Dps' }
            if ($family -notin @('X509Dps', 'X509DpsAdr')) { throw 'Unsupported provisioning family.' }
            if ($family -eq 'X509DpsAdr') {
                foreach ($name in @('bootstrapCert', 'bootstrapKey', 'deviceKey', 'csr')) {
                    if (-not (Test-Path -LiteralPath (Config-Path $folder (Field $config $name)) -PathType Leaf)) { throw 'Missing ADR path.' }
                }
                $cert = Field $state 'issuedCert'
                # StateStore does not resolve relative paths, unlike load_config.
                if ($cert -isnot [string] -or -not [IO.Path]::IsPathRooted($cert) -or -not (Test-Path -LiteralPath $cert -PathType Leaf)) { throw 'Missing absolute issuedCert.' }
                $issuedAt = Field $state 'adrIssuedAt'
                if ($issuedAt -isnot [ValueType] -or $issuedAt -is [bool] -or $issuedAt -is [datetime]) { throw 'Missing issuance epoch.' }
                $renewAfter = Field $config 'adrRenewAfterSeconds'
                if ($null -eq $renewAfter) { $renewAfter = 60 * 86400 }
                $margin = Field $config 'adrRenewBeforeSeconds'
                if ($null -eq $margin) { $margin = 7 * 86400 }
                if ($renewAfter -isnot [ValueType] -or $renewAfter -is [bool] -or $renewAfter -le 0 -or $margin -isnot [ValueType] -or $margin -is [bool] -or $margin -lt 0) { throw 'Invalid ADR timing.' }
                $renewAt = [double]$issuedAt + [double]$renewAfter
                $expiryFile = Field $config 'certificateExpiryFile'
                if ($expiryFile) { $expiryFile = Config-Path $folder $expiryFile }
                else { $expiryFile = if ($cert.EndsWith('.pem')) { $cert.Substring(0, $cert.Length - 4) + '.expiry.json' } else { $cert + '.expiry.json' } }
                if (Test-Path -LiteralPath $expiryFile) {
                    $expiry = Object-Json $expiryFile
                    if ((Field $expiry 'certificateSha256') -ceq (Get-FileHash -LiteralPath $cert -Algorithm SHA256).Hash.ToLowerInvariant()) {
                        $date = [datetime]::ParseExact((Field $expiry 'notAfter'), "yyyy-MM-ddTHH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal)
                        $renewAt = ([datetimeoffset]$date).ToUnixTimeSeconds() - [double]$margin
                    }
                    # A mismatched digest uses the same fallback as certificate_renewal_at.
                }
                if ($renewAt -le [datetimeoffset]::UtcNow.ToUnixTimeSeconds()) { throw 'ADR renewal due; offline cached start is prohibited.' }
            }
            Add-Check $label $true 'Config readable; DPS false; paths, paho and cached assignment ready.'
        } catch { Add-Check $label $false 'Unreadable/unsafe config, missing dependency/cache or ADR renewal due; inspect locally without sharing secrets.' }
    }
} catch { Add-Check 'Devices root/configs' $false 'Cannot enumerate external device root.' }
try {
    # Full command lines are inspected in memory ONLY, never emitted.
    $processes = @(Get-CimInstance Win32_Process)
    $running = @($processes | Where-Object {
        $_.Name -like 'pysharp*' -or ($_.Name -eq 'dotnet.exe' -and $_.CommandLine -match '(?i)pysharp')
    })
    Add-Check 'PySharp processes' ($running.Count -eq 0) ('Active agent/worker/tool processes: ' + $running.Count + '. Coordinate ownership before any start/test.')
} catch { Add-Check 'PySharp processes' $false 'Cannot inspect process inventory; do not assume no devices are running.' }
$rows | Format-Table -AutoSize -Wrap | Out-Host
if (@($rows | Where-Object { $_.Status -eq 'BLOCK' }).Count) { exit 1 }
exit 0
