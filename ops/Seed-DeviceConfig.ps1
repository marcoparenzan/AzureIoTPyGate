<#
.SYNOPSIS
Creates only missing agent config, cached ADR assignment and expiry metadata, offline.
.DESCRIPTION
Requires one existing credential package and a real X509DpsAdr template. No discovery,
registration, key reads, key generation, secret output or overwrite. Use -WhatIf first.
Copyright (c) 2026 Marco Parenzan. MIT License.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory)][string]$CredentialFolder,
    [Parameter(Mandatory)][string]$TemplateConfig
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Read-Object([string]$Path) {
    $value = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ($null -eq $value -or $value -isnot [pscustomobject]) { throw 'Expected a JSON object.' }
    return $value
}
function Get-Field($Object, [string]$Name) {
    $property = $Object.PSObject.Properties[$Name]
    if ($null -ne $property) { return $property.Value }
    return $null
}
function Require-Text($Value) {
    if ($Value -isnot [string] -or [string]::IsNullOrWhiteSpace($Value) -or $Value.Contains('<')) { throw 'Missing real text field.' }
}
function Assert-PlainFile([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Expected a regular file.' }
}
function Read-Leaf([string]$Path) {
    $pem = [IO.File]::ReadAllText($Path)
    $match = [regex]::Match($pem, '-----BEGIN CERTIFICATE-----\s*(?<body>[A-Za-z0-9+/=\s]+)-----END CERTIFICATE-----')
    if (-not $match.Success) { throw 'Certificate PEM not found.' }
    $bytes = [Convert]::FromBase64String($match.Groups['body'].Value)
    return [System.Security.Cryptography.X509Certificates.X509Certificate2]::new($bytes)
}
function Write-MissingJson([string]$Path, $Value) {
    # CreateNew is the final no-clobber guard, including a concurrent writer.
    # The package must not be used by a running device during seeding.
    if ([IO.File]::Exists($Path)) { Write-Output ('SKIP existing ' + [IO.Path]::GetFileName($Path)); return }
    if ($PSCmdlet.ShouldProcess([IO.Path]::GetFileName($Path), 'Create missing offline metadata')) {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes(($Value | ConvertTo-Json -Depth 32))
        $stream = [IO.File]::Open($Path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($bytes, 0, $bytes.Length); $stream.Flush() } finally { $stream.Dispose() }
        Write-Output ('CREATED ' + [IO.Path]::GetFileName($Path))
    }
}
try {
    $folder = (Get-Item -LiteralPath $CredentialFolder -Force)
    if (-not $folder.PSIsContainer -or ($folder.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Expected a regular credential directory.' }
    $folder = $folder.FullName
    # Do not follow junctions in the package ancestry either.
    $parent = Get-Item -LiteralPath $folder -Force
    while ($null -ne $parent) {
        if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse-point ancestry is unsupported.' }
        $parent = $parent.Parent
    }
    foreach ($name in @('dps-registration.json', 'issued-chain.pem', 'bootstrap-chain.pem', 'bootstrap-private-key.pem', 'device-private-key.pem', 'device.csr.pem')) {
        Assert-PlainFile (Join-Path $folder $name) # Keys are checked for existence only, never opened.
    }
    Assert-PlainFile $TemplateConfig
    $templatePath = (Get-Item -LiteralPath $TemplateConfig).FullName
    $template = Read-Object $templatePath
    $registration = Get-Field (Read-Object (Join-Path $folder 'dps-registration.json')) 'registrationState'
    if ($registration -isnot [pscustomobject] -or (Get-Field $registration 'status') -cne 'assigned') { throw 'An assigned registrationState is required.' }
    $device = Get-Field $registration 'deviceId'
    $hub = Get-Field $registration 'assignedHub'
    Require-Text $device
    Require-Text $hub
    if ($device -cnotmatch '^[A-Za-z0-9_-]+$') { throw 'Invalid device identifier.' }
    $registrationId = Get-Field $registration 'registrationId'
    if ($null -ne $registrationId -and $registrationId -cne $device) { throw 'Registration identity mismatch.' }
    if ((Get-Field $template 'identityFamily') -cne 'X509DpsAdr') { throw 'Use a real X509DpsAdr template.' }
    foreach ($name in @('idScope', 'globalEndpoint', 'payloadDir')) { Require-Text (Get-Field $template $name) }
    foreach ($name in @('adrRenewBeforeSeconds', 'adrRenewAfterSeconds')) {
        $value = Get-Field $template $name
        if ($null -ne $value -and ($value -isnot [ValueType] -or $value -is [bool] -or $value -is [datetime] -or $value -lt 0 -or ($name -eq 'adrRenewAfterSeconds' -and $value -le 0))) { throw 'Invalid ADR renewal timing.' }
    }
    $chainPath = Join-Path $folder 'issued-chain.pem'
    $certificate = Read-Leaf $chainPath
    try {
        $leafPath = Join-Path $folder 'issued-chain-01.pem'
        if (Test-Path -LiteralPath $leafPath) {
            Assert-PlainFile $leafPath
            $leaf = Read-Leaf $leafPath
            try { if ([Convert]::ToBase64String($leaf.RawData) -cne [Convert]::ToBase64String($certificate.RawData)) { throw 'Bundle must be leaf-first.' } } finally { $leaf.Dispose() }
        }
        $notBefore = $certificate.NotBefore.ToUniversalTime()
        $notAfter = $certificate.NotAfter.ToUniversalTime()
        $margin = Get-Field $template 'adrRenewBeforeSeconds'
        if ($null -eq $margin) { $margin = 7 * 86400 }
        if ($notBefore -gt [datetime]::UtcNow -or $notAfter.AddSeconds(-[double]$margin) -le [datetime]::UtcNow) { throw 'Certificate is not ready for a cached ADR start.' }
        $expiry = [ordered]@{
            notAfter = $notAfter.ToString("yyyy-MM-ddTHH:mm:ss'Z'", [Globalization.CultureInfo]::InvariantCulture)
            certificateSha256 = (Get-FileHash -LiteralPath $chainPath -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        $issuedAt = ([datetimeoffset]$notBefore).ToUnixTimeSeconds()
    } finally { $certificate.Dispose() }
    $configPath = Join-Path $folder 'agent-config.json'
    $statePath = Join-Path $folder 'agent-state.json'
    $expiryPath = Join-Path $folder 'issued-chain.expiry.json'
    $config = [ordered]@{}
    foreach ($property in $template.PSObject.Properties) { $config[$property.Name] = $property.Value }
    # Preserve template settings, but resolve shared relative paths against the template, not the new package.
    foreach ($name in @('payloadDir', 'payloadPublicKeyPem', 'pahoSite')) {
        if ($config.Contains($name) -and $config[$name]) {
            if (-not [IO.Path]::IsPathRooted($config[$name])) { $config[$name] = [IO.Path]::GetFullPath((Join-Path (Split-Path $templatePath -Parent) $config[$name])) }
        }
    }
    $config['registrationId'] = $device
    $config['stateFile'] = $statePath
    $config['csr'] = Join-Path $folder 'device.csr.pem'
    $config['bootstrapKey'] = Join-Path $folder 'bootstrap-private-key.pem'
    $config['bootstrapCert'] = Join-Path $folder 'bootstrap-chain.pem'
    $config['key'] = $config['bootstrapKey']
    $config['cert'] = $config['bootstrapCert']
    $config['deviceKey'] = Join-Path $folder 'device-private-key.pem'
    $config['otaDir'] = Join-Path $folder 'ota'
    $config['certificateExpiryFile'] = $expiryPath
    $config['allowDpsRegistration'] = $false
    $config['allowReprovision'] = $false
    $state = [ordered]@{ assignedHub = $hub; deviceId = $device; issuedCert = $chainPath; adrIssuedAt = $issuedAt }
    # Validate retained files before writing anything; never silently repair an unsafe package.
    foreach ($path in @($configPath, $statePath, $expiryPath)) { if (Test-Path -LiteralPath $path) { Assert-PlainFile $path } }
    if (Test-Path -LiteralPath $configPath) {
        $existing = Read-Object $configPath
        $allow = Get-Field $existing 'allowDpsRegistration'
        if ($allow -isnot [bool] -or $allow -ne $false) { throw 'Existing config is unsafe.' }
        foreach ($name in @('registrationId', 'identityFamily', 'idScope', 'globalEndpoint', 'stateFile', 'certificateExpiryFile', 'deviceKey', 'cert', 'key', 'bootstrapCert', 'bootstrapKey', 'csr')) {
            $actual = Get-Field $existing $name
            if ($name -in @('stateFile', 'certificateExpiryFile', 'deviceKey', 'cert', 'key', 'bootstrapCert', 'bootstrapKey', 'csr') -and $actual -is [string] -and -not [IO.Path]::IsPathRooted($actual)) { $actual = [IO.Path]::GetFullPath((Join-Path $folder $actual)) }
            if ($actual -cne $config[$name]) { throw 'Existing config does not match the package/template.' }
        }
    }
    if (Test-Path -LiteralPath $statePath) {
        $existing = Read-Object $statePath
        foreach ($name in @('assignedHub', 'deviceId', 'issuedCert', 'adrIssuedAt')) { if ((Get-Field $existing $name) -cne $state[$name]) { throw 'Existing state does not match the package.' } }
    }
    if (Test-Path -LiteralPath $expiryPath) {
        $existing = Read-Object $expiryPath
        foreach ($name in @('notAfter', 'certificateSha256')) { if ((Get-Field $existing $name) -cne $expiry[$name]) { throw 'Existing expiry metadata does not match the certificate.' } }
    }
    # Config last: a failed earlier write must not advertise a ready package.
    Write-MissingJson $expiryPath $expiry
    Write-MissingJson $statePath $state
    Write-MissingJson $configPath $config
} catch {
    # Never echo exception bodies, JSON, certificate material, keys, paths or assignments.
    Write-Output 'BLOCKED: offline seed validation/write failed; existing files were not overwritten. Check package, template, certificate lifetime and permissions.'
    exit 1
}
