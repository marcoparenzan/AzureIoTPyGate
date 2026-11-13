// Copyright (c) 2026 Marco Parenzan. MIT License.
//
// Starts one simulated device (the Python agent) under PySharp, outside Forge. A .NET file-based app: no project file.
//
//   dotnet run scripts/StartDevice.cs -- device01                  runs until you press Ctrl+C
//   dotnet run scripts/StartDevice.cs -- device01 --seconds 120    stops cleanly after two minutes
//   dotnet run scripts/StartDevice.cs -- device01 --dry-run        runs the checks and prints the command, starts nothing
//   dotnet run scripts/StartDevice.cs -- device01 --stop           asks the running agent to stop cleanly (stop.request file), waits 30 s
//   dotnet run scripts/StartDevice.cs -- device01 --log-level debug
//   -LogLevel is also accepted. Precedence: flag, config logLevel, info; passed as PYGATE_LOG_LEVEL.
//   At debug/trace the agent logger prints its authoritative log file path on the inherited console.
//
// It reads <config root>\devices\<id>\agent-config.json (config root: %FORGE_CONFIG_ROOT% or D:\Configurations\AzureIoTForge).
// Nothing is registered with DPS: the agent starts from the assignment already cached in the device folder. It refuses to start when:
//   - the config does not say "allowDpsRegistration": false (a registration changes Azure);
//   - pysharp is not on PATH;
//   - an agent for this device is already running (IoT Hub allows one connection per device id: a second copy would keep
//     kicking the first one off the hub).
// If the agent exits with code 75 it asked for a restart, and this program starts it again.

using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.RegularExpressions;

// ---- 1. Arguments
string? deviceId = null;
int? seconds = null;
string? logLevel = null;
var dryRun = false;
var stopRequested = false;
for (var i = 0; i < args.Length; i++)
{
    switch (args[i])
    {
        case "--seconds" when i + 1 < args.Length && int.TryParse(args[i + 1], out var s) && s > 0: seconds = s; i++; break;
        case "--dry-run": dryRun = true; break;
        case "--stop": stopRequested = true; break;
        case "--log-level":
        case "-LogLevel":
            if (i + 1 >= args.Length || !IsLogLevel(args[i + 1]))
                return Fail("Invalid log level flag. Expected off, info, debug or trace.", 2);
            logLevel = args[++i];
            break;
        case var a when !a.StartsWith("--") && deviceId is null: deviceId = a; break;
        default: return Fail($"Unexpected argument '{args[i]}'. Usage: StartDevice.cs <deviceId> [--seconds N] [--log-level off|info|debug|trace] [--dry-run]", 2);
    }
}
if (deviceId is null || !Regex.IsMatch(deviceId, "^[A-Za-z0-9_-]+$"))
    return Fail("Give a device id made of letters, digits, - and _. Usage: StartDevice.cs <deviceId> [--seconds N] [--log-level off|info|debug|trace] [--dry-run]", 2);

// ---- 2. The device's config file
var configRoot = Environment.GetEnvironmentVariable("PYGATE_DEVICES_ROOT") ?? Path.Combine(Environment.GetEnvironmentVariable("FORGE_CONFIG_ROOT") ?? @"D:\Configurations\AzureIoTForge", "devices"); // legacy fallback
var configPath = Path.GetFullPath(Path.Combine(configRoot, deviceId, "agent-config.json")); // full path: the duplicate guard matches it in the command line
if (!File.Exists(configPath)) return Fail($"Config not found: {configPath}", 2);

// ---- 3. Never register with DPS from here
using (var config = JsonDocument.Parse(File.ReadAllText(configPath)))
{
    if (!config.RootElement.TryGetProperty("allowDpsRegistration", out var allow) || allow.ValueKind != JsonValueKind.False)
        return Fail($"Refusing to start: {configPath} must contain \"allowDpsRegistration\": false", 2);

    // Resolve once for all restarts; do not take the inherited environment as a source.
    if (logLevel is null)
    {
        if (config.RootElement.TryGetProperty("logLevel", out var configuredLevel))
        {
            if (configuredLevel.ValueKind != JsonValueKind.String || !IsLogLevel(configuredLevel.GetString()))
                return Fail("Invalid config logLevel. Expected off, info, debug or trace.", 2);
            logLevel = configuredLevel.GetString();
        }
        else logLevel = "info";
    }
}

// ---- 4. PySharp and the agent
var pysharp = (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator)
    .Select(folder => Path.Combine(folder, "pysharp.exe")).FirstOrDefault(File.Exists);
if (pysharp is null) return Fail("pysharp is not on PATH (check: dotnet tool list -g).", 2);
var agentDir = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(ThisFile())!, "..", "src", "agent"));
if (!File.Exists(Path.Combine(agentDir, "main.py"))) return Fail($"Agent not found: {agentDir}\\main.py", 2);

// ---- 5. Not already running (started here, or by Forge)
if (stopRequested)
{
    if (!IsRunning(deviceId, configPath)) { Console.WriteLine($"{deviceId} is not running."); return 0; }
    var flag = Path.Combine(Path.GetDirectoryName(configPath)!, "stop.request");
    File.WriteAllText(flag, "stop");
    Console.WriteLine($"Stop requested for {deviceId} ({flag}). Waiting up to 30 s for a clean exit...");
    var until = DateTime.UtcNow.AddSeconds(30);
    while (IsRunning(deviceId, configPath) && DateTime.UtcNow < until) Thread.Sleep(500);
    if (IsRunning(deviceId, configPath)) return Fail($"{deviceId} is still running after 30 s. Use Ctrl+C in its own console, or ask the owner.", 4);
    Console.WriteLine($"{deviceId} stopped.");
    return 0;
}
if (IsRunning(deviceId, configPath))
    return Fail($"{deviceId} is already running (started here or by Forge). Stop it first: a second copy would fight with it for the hub connection.", 3);

// ---- 6. Run, and start again when the agent asks for a restart (exit code 75)
var arguments = new List<string> { "run", "main.py", "--live", configPath };
if (seconds is { } n) arguments.Add(n.ToString());
if (dryRun)
{
    Console.WriteLine($"Dry run. Child environment: PYGATE_LOG_LEVEL={logLevel}");
    Console.WriteLine($"Dry run. Would run, in {agentDir}:  pysharp {string.Join(' ', arguments)}");
    return 0;
}

// Ctrl+C goes to the agent (same console); this program waits for it to stop cleanly.
Console.CancelKeyPress += (_, e) => e.Cancel = true;
int exitCode;
do
{
    Console.WriteLine($"[{DateTime.Now:HH:mm:ss}] starting {deviceId}");
    var startInfo = new ProcessStartInfo(pysharp, arguments) { WorkingDirectory = agentDir, UseShellExecute = false };
    startInfo.Environment["PYGATE_LOG_LEVEL"] = logLevel!;
    // The agent logger owns logDir confinement/rotation and prints the actual file at debug/trace.
    // Keep its console inherited rather than guessing a filename here.
    using var agent = Process.Start(startInfo)!;
    agent.WaitForExit();
    exitCode = agent.ExitCode;
    Console.WriteLine($"[{DateTime.Now:HH:mm:ss}] {deviceId} exited with code {exitCode}");
} while (exitCode == 75);
return exitCode;

// ---- helpers
static bool IsLogLevel(string? level) => level is "off" or "info" or "debug" or "trace";

static int Fail(string message, int code)
{
    Console.Error.WriteLine(message);
    return code;
}

static string ThisFile([CallerFilePath] string path = "") => path;

static bool IsRunning(string id, string configPath)
{
    // Matches this exact config path (any devices root) or the legacy \devices\<id>\ layout.
    // Values travel in environment variables, so no quoting of the path is needed.
    const string query = "$p=[WildcardPattern]::Escape($env:PYGATE_GUARD_PATH); $l='*\\devices\\'+$env:PYGATE_GUARD_ID+'\\agent-config.json*'; " +
        "if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pysharp.exe' -and $_.CommandLine -and ($_.CommandLine -like ('*'+$p+'*') -or $_.CommandLine -like $l) }) { exit 1 } else { exit 0 }";
    var info = new ProcessStartInfo("powershell", ["-NoProfile", "-Command", query]) { UseShellExecute = false, CreateNoWindow = true };
    info.Environment["PYGATE_GUARD_PATH"] = configPath;
    info.Environment["PYGATE_GUARD_ID"] = id;
    using var check = Process.Start(info)!;
    check.WaitForExit();
    return check.ExitCode == 1;
}
