<#
.SYNOPSIS
    Starts one simulated device (the Python agent) under PySharp, outside Forge.

.EXAMPLE
    .\scripts\Start-Device.ps1 device01          # runs until you press Ctrl+C
    .\scripts\Start-Device.ps1 device01 -Seconds 120

.EXAMPLE
    # three devices, three windows
    'device01','device02','device03' | ForEach-Object { Start-Process pwsh -ArgumentList '-NoExit','-File',".\scripts\Start-Device.ps1",$_ }

.DESCRIPTION
    Copyright (c) 2026 Marco Parenzan. MIT License.

    Reads <config root>\devices\<DeviceId>\agent-config.json. The config root is $env:FORGE_CONFIG_ROOT,
    or D:\Configurations\AzureIoTForge. Nothing is registered with DPS: the agent starts from the
    assignment already cached in the device folder.

    It refuses to start when:
      - the config is missing, or does not say "allowDpsRegistration": false (a registration changes Azure);
      - pysharp is not on PATH;
      - an agent for this device is already running (IoT Hub allows one connection per device id, so a second
        copy keeps kicking the first one off the hub).
    If the agent exits with code 75 it asked for a restart, and this script starts it again.
.PARAMETER DeviceId
    Folder name under <config root>\devices, for example device01.
.PARAMETER Seconds
    Stop cleanly after this many seconds. Without it the device runs until Ctrl+C.
.PARAMETER LogLevel
    off, info, debug or trace. Overrides config logLevel (default info) through PYGATE_LOG_LEVEL.
    At debug/trace the agent logger prints its authoritative log file path on the inherited console.
.PARAMETER Stop
    Ask the running agent of this device to stop cleanly (creates stop.request next to agent-config.json, which the
    agent polls about once a second), wait up to 30 seconds, and exit. Starts nothing. Exit 0 stopped or not running,
    4 if it was still running after 30 seconds.
.PARAMETER DryRun
    Run every check and print the command and effective logging level, but do not start the device.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$DeviceId,

    [ValidateRange(1, 86400)]
    [int]$Seconds,

    [switch]$DryRun,

    [switch]$Stop,

    [string]$LogLevel
)

$ErrorActionPreference = 'Stop'

function Stop-Here([string]$Message, [int]$Code) {
    Write-Host $Message -ForegroundColor Red
    exit $Code
}

# Validate an explicit level before any device/process checks. Do not echo an invalid value.
$logLevels = @('off', 'info', 'debug', 'trace')
if ($PSBoundParameters.ContainsKey('LogLevel') -and $logLevels -cnotcontains $LogLevel) {
    Stop-Here 'Invalid -LogLevel. Expected off, info, debug or trace.' 2
}

# 1. The device's config file
$configRoot = if ($env:PYGATE_DEVICES_ROOT) { $env:PYGATE_DEVICES_ROOT } elseif ($env:FORGE_CONFIG_ROOT) { Join-Path $env:FORGE_CONFIG_ROOT 'devices' } else { 'D:\Configurations\AzureIoTForge\devices' } # legacy fallback
$configPath = Join-Path $configRoot "$DeviceId\agent-config.json"
if (-not (Test-Path $configPath)) { Stop-Here "Config not found: $configPath" 2 }
# Full path: the duplicate guard matches it in the process command line, whatever the devices root is called.
$configPath = (Resolve-Path -LiteralPath $configPath).ProviderPath

# 2. Never register with DPS from here
$config = Get-Content $configPath -Raw | ConvertFrom-Json
if ($config.allowDpsRegistration -ne $false) {
    Stop-Here "Refusing to start: $configPath must contain `"allowDpsRegistration`": false" 2
}

# Resolve once so every restart receives the same level; inherited PYGATE_LOG_LEVEL is not a source.
$effectiveLogLevel = 'info'
if ($PSBoundParameters.ContainsKey('LogLevel')) {
    $effectiveLogLevel = $LogLevel
} elseif ($null -ne $config.PSObject.Properties['logLevel']) {
    $effectiveLogLevel = $config.logLevel
}
if ($effectiveLogLevel -isnot [string] -or $logLevels -cnotcontains $effectiveLogLevel) {
    Stop-Here 'Invalid config logLevel. Expected off, info, debug or trace.' 2
}

# 3. PySharp and the agent
if (-not (Get-Command pysharp -ErrorAction SilentlyContinue)) { Stop-Here 'pysharp is not on PATH (check: dotnet tool list -g).' 2 }
$agentDir = Join-Path $PSScriptRoot '..\src\agent' | Resolve-Path
if (-not (Test-Path (Join-Path $agentDir 'main.py'))) { Stop-Here "Agent not found: $agentDir\main.py" 2 }

# 4. Not already running (started here, or by Forge)
$running = Get-CimInstance Win32_Process |
    Where-Object {
        $_.Name -eq 'pysharp.exe' -and $_.CommandLine -and (
            $_.CommandLine -like ('*' + [WildcardPattern]::Escape($configPath) + '*') -or   # this exact config, any devices root
            $_.CommandLine -like "*\devices\$DeviceId\agent-config.json*")                 # legacy layout (for example Forge)
    }
if ($Stop) {
    if (-not $running) { Write-Host "$DeviceId is not running."; exit 0 }
    $flag = Join-Path (Split-Path -Parent $configPath) 'stop.request'
    Set-Content -LiteralPath $flag -Value 'stop' -Encoding ascii
    Write-Host "Stop requested for $DeviceId ($flag). Waiting up to 30 s for a clean exit..."
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        $still = Get-CimInstance Win32_Process | Where-Object {
            $_.Name -eq 'pysharp.exe' -and $_.CommandLine -and (
                $_.CommandLine -like ('*' + [WildcardPattern]::Escape($configPath) + '*') -or
                $_.CommandLine -like "*\devices\$DeviceId\agent-config.json*")
        }
    } while ($still -and (Get-Date) -lt $deadline)
    if ($still) { Stop-Here "$DeviceId is still running after 30 s. Use Ctrl+C in its own console, or ask the owner." 4 }
    Write-Host "$DeviceId stopped." -ForegroundColor Green
    exit 0
}
if ($running) {
    Stop-Here "$DeviceId is already running (started here or by Forge). Stop it first: a second copy would fight with it for the hub connection." 3
}

# 5. Run, and start again when the agent asks for a restart (exit code 75)
$arguments = @('run', 'main.py', '--live', $configPath)
if ($Seconds) { $arguments += $Seconds }

if ($DryRun) {
    Write-Host "Dry run. Child environment: PYGATE_LOG_LEVEL=$effectiveLogLevel" -ForegroundColor Yellow
    Write-Host "Dry run. Would run, in ${agentDir}:  pysharp $($arguments -join ' ')" -ForegroundColor Yellow
    exit 0
}

# The agent logger owns logDir confinement/rotation and prints the actual file at debug/trace.
# Keep its console inherited rather than guessing a filename here.
$previousLogLevel = $env:PYGATE_LOG_LEVEL
Push-Location $agentDir
try {
    $env:PYGATE_LOG_LEVEL = $effectiveLogLevel
    do {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] starting $DeviceId" -ForegroundColor Cyan
        & pysharp @arguments
        $exitCode = $LASTEXITCODE
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $DeviceId exited with code $exitCode" -ForegroundColor Cyan
    } while ($exitCode -eq 75)
}
finally {
    $env:PYGATE_LOG_LEVEL = $previousLogLevel
    Pop-Location
}
exit $exitCode
