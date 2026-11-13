#:property TargetFramework=net11.0
#:property Nullable=enable
#:property ImplicitUsings=enable
// Copyright (c) 2026 Marco Parenzan. MIT License.
// Read-only PowerShell bridge: default source checks, optional tool inspection or preflight.
// dotnet run --file ops/Validate-Ops.cs --artifacts-path D:\Dev\BuildArtifacts\AzureIoTPyGate\ops-validation
// Append -- --inspect-tools for version/help queries, or -- --preflight for the read-only table.
using System.Diagnostics;
using System.Runtime.CompilerServices;

var root = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(Source())!, ".."));
var shell = OperatingSystem.IsWindows() ? "powershell.exe" : "pwsh";
var info = new ProcessStartInfo(shell) { UseShellExecute = false, WorkingDirectory = root };
info.ArgumentList.Add("-NoProfile");
info.ArgumentList.Add("-NonInteractive");
info.ArgumentList.Add("-File");
var preflight = args.Contains("--preflight");
info.ArgumentList.Add(preflight
    ? Path.Combine(root, "scripts", "Preflight.ps1")
    : Path.Combine(root, "ops", "Validate-Ops.ps1"));
if (!preflight && args.Contains("--inspect-tools")) info.ArgumentList.Add("-InspectTools");
using var process = Process.Start(info)!;
process.WaitForExit();
return process.ExitCode;
static string Source([CallerFilePath] string path = "") => path;
