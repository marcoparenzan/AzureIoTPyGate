# Copyright (c) 2026 Marco Parenzan. MIT License.
# Only parses scripts and reads sources/tool metadata. Never invokes operational scripts.
[CmdletBinding()]
param([switch]$InspectTools)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$failed = $false
foreach ($file in @((Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1' -File)) + @((Get-Item -LiteralPath (Join-Path $root 'scripts/Preflight.ps1') -ErrorAction SilentlyContinue))) {
    $tokens = $null
    $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { $failed = $true; Write-Output ($file.Name + ': FAIL syntax (' + $errors.Count + ')') }
    else { Write-Output ($file.Name + ': PASS syntax') }
}
# Contract checks are static: no seeding, installs, real config reads or device starts.
function Assert-Contract([string]$Name, [bool]$Condition) {
    if ($Condition) { Write-Output ($Name + ': PASS source contract') }
    else { $script:failed = $true; Write-Output ($Name + ': FAIL source contract') }
}
$seedPath = Join-Path $PSScriptRoot 'Seed-DeviceConfig.ps1'
if (Test-Path -LiteralPath $seedPath) {
    $seed = [IO.File]::ReadAllText($seedPath)
    $configSource = [IO.File]::ReadAllText((Join-Path $root 'src/agent/config.py'))
    $dpsSource = [IO.File]::ReadAllText((Join-Path $root 'src/agent/dps.py'))
    $reference = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'seed-device-config.reference.py'))
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($seedPath, [ref]$tokens, [ref]$errors)
    $assignments = @($ast.FindAll({ param($a) $a -is [System.Management.Automation.Language.AssignmentStatementAst] }, $true))
    foreach ($name in @('allowDpsRegistration', 'allowReprovision')) {
        $assignment = @($assignments | Where-Object { $_.Left.Extent.Text -ceq ("`$config['" + $name + "']") })
        Assert-Contract ($name + ' boolean false') ($assignment.Count -eq 1 -and $assignment[0].Right.Extent.Text -ceq '$false' -and $reference.Contains('"' + $name + '": False'))
    }
    foreach ($pair in @(
        @{ Variable = '$state'; Keys = @('assignedHub', 'deviceId', 'issuedCert', 'adrIssuedAt') },
        @{ Variable = '$expiry'; Keys = @('notAfter', 'certificateSha256') }
    )) {
        $assignment = @($assignments | Where-Object { $_.Left.Extent.Text -ceq $pair.Variable })
        $tables = @($assignment[0].Right.FindAll({ param($a) $a -is [System.Management.Automation.Language.HashtableAst] }, $true))
        $keys = @($tables[0].KeyValuePairs | ForEach-Object { $_.Item1.SafeGetValue() })
        Assert-Contract ($pair.Variable + ' exact schema') (@(Compare-Object $keys $pair.Keys).Count -eq 0)
        foreach ($key in $pair.Keys) {
            Assert-Contract ($key + ' consumed/source reference') ($dpsSource.Contains('"' + $key + '"') -and $reference.Contains('"' + $key + '"'))
        }
    }
    foreach ($name in @('registrationId', 'stateFile', 'csr', 'bootstrapKey', 'bootstrapCert', 'key', 'cert', 'deviceKey', 'certificateExpiryFile')) {
        Assert-Contract ($name + ' config field') ($seed.Contains("`$config['" + $name + "']") -and $configSource.Contains('"' + $name + '"') -and $reference.Contains('"' + $name + '"'))
    }
    Assert-Contract 'ADR family explicit' ($seed.Contains("-cne 'X509DpsAdr'") -and $configSource.Contains('"X509DpsAdr"'))
    Assert-Contract 'Bundle byte SHA256 (not leaf)' ($seed.Contains('Get-FileHash -LiteralPath $chainPath -Algorithm SHA256') -and $dpsSource.Contains('hashlib.sha256(f.read()).hexdigest()'))
    Assert-Contract 'UTC expiry plus Unix issuance' ($seed.Contains('yyyy-MM-ddTHH:mm:ss') -and $seed.Contains("'Z'") -and $seed.Contains('ToUnixTimeSeconds()') -and $reference.Contains('not_before.timestamp()'))
    Assert-Contract 'Missing-only exclusive writes' ($seed.Contains('[IO.FileMode]::CreateNew') -and $seed.Contains('[IO.File]::Exists($Path)') -and $seed.Contains('SupportsShouldProcess = $true'))
    Assert-Contract 'Credential folder parameter/no device list' ($seed.Contains('[string]$CredentialFolder') -and -not ($seed -match 'device0[123]|D:\\\\Configurations'))
    $commands = @($ast.FindAll({ param($a) $a -is [System.Management.Automation.Language.CommandAst] }, $true) | ForEach-Object { $_.GetCommandName() })
    Assert-Contract 'Seed offline command boundary' (@($commands | Where-Object { $_ -match '^(az|pysharp|dotnet|curl|wget|Invoke-WebRequest|Invoke-RestMethod|Start-Process|Stop-Process)$' }).Count -eq 0)
}
$installPath = Join-Path $PSScriptRoot 'Install-Dependencies.ps1'
if (Test-Path -LiteralPath $installPath) {
    $install = [IO.File]::ReadAllText($installPath)
    Assert-Contract 'Pinned private install' ($install.Contains("& pysharp install 'paho-mqtt==2.1.0'") -and $install.Contains('Push-Location $stage') -and $install.Contains("Join-Path `$stage 'site-packages'") -and $install.Contains('$env:PYGATE_PYTHON_SITE') -and -not ($install -match 'tool (install|update)|pip install'))
}
$preflightPath = Join-Path $root 'scripts/Preflight.ps1'
if (Test-Path -LiteralPath $preflightPath) {
    $text = [IO.File]::ReadAllText($preflightPath)
    Assert-Contract 'Preflight read-only command boundary' (-not ($text -match '\b(Invoke-WebRequest|Invoke-RestMethod|Start-Process|Stop-Process|Set-Content|Out-File|New-Item|Remove-Item)\b') -and $text.Contains('Format-Table') -and $text.Contains('& pysharp --version') -and -not $text.Contains('& pysharp run'))
}
if ($InspectTools) {
    & dotnet --list-sdks
    & dotnet tool list -g
    # CLI help only; never the install command without --help.
    if (Get-Command pysharp -ErrorAction SilentlyContinue) { & pysharp --version; & pysharp install --help }
}
if ($failed) { exit 1 }
exit 0
