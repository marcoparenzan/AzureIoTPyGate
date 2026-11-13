// Copyright (c) 2026 Marco Parenzan
// Licensed under the MIT License. See LICENSE in the project root.
using System.Diagnostics;
using System.Text;

namespace AzureIoTPyGate.Tests;

public sealed class DeviceAgentTests
{
    private const string RecoveryRoot = @"D:\Dev\BuildArtifacts\AzureIoTPyGate\tmp\worker-recovery";

    [Fact]
    public async Task InspectSyntheticLogsWithoutSecrets()
    {
        // This verifies count-only inspection, not historical live reliability.
        var scratch = Path.Combine(RecoveryRoot, "synthetic-logs", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(scratch);
        var path = Path.Combine(scratch, "redacted.log");
        await File.WriteAllTextAsync(path, "System.IO.IOException: used by another process\nworker_exited\nwatchdog_timeout\npayloadErrors=1\nworker_exited\n");
        var text = await File.ReadAllTextAsync(path);
        var counters = new Dictionary<string, int>();
        foreach (var marker in new[] { "System.IO.IOException", "used by another process", "worker_exited", "watchdog_timeout", "payloadErrors" })
            counters[marker] = System.Text.RegularExpressions.Regex.Matches(text, System.Text.RegularExpressions.Regex.Escape(marker)).Count;
        Assert.Equal(1, counters["System.IO.IOException"]);
        Assert.Equal(1, counters["used by another process"]);
        Assert.Equal(2, counters["worker_exited"]);
        Assert.Equal(1, counters["watchdog_timeout"]);
        Assert.Equal(1, counters["payloadErrors"]);
        var summary = System.Text.Json.JsonSerializer.Serialize(counters);
        await File.WriteAllTextAsync(Path.Combine(scratch, "inspection.json"), summary);
        Assert.DoesNotContain("payloadErrors=1", summary);
    }

    [Theory]
    [InlineData("test_worker_windows_contention")]
    [InlineData("test_worker_death_recovery")]
    [InlineData("test_worker_ipc_retention")]
    [InlineData("test_worker_redacted_output")]
    [InlineData("test_worker_heartbeat_idle")]
    [InlineData("test_dps_happy_retry_cache")]
    [InlineData("test_dps_throttle_timeout_permission")]
    [InlineData("test_desired_convergence_stale_validation")]
    [InlineData("test_alarm_hysteresis")]
    [InlineData("test_report_throttle_coalesce_temperature")]
    [InlineData("test_payload_exception_containment")]
    [InlineData("test_methods")]
    [InlineData("test_reconnect_backoff_reprovision")]
    [InlineData("test_state_encoder_and_loader")]
    [InlineData("test_state_save_failure_retried")]
    [InlineData("test_stop_file_requests_clean_stop")]
    [InlineData("test_ten_minutes_virtual_disconnect")]
    [InlineData("test_reboot_process_exit")]
    [InlineData("test_mqtt_callbacks")]
    [InlineData("test_ota_bootstrap_safe_state")]
    [InlineData("test_ota_previous_accepted_rollback")]
    [InlineData("test_ota_desired_tick_watchdog_rollback")]
    [InlineData("test_worker_replacement_initialization")]
    [InlineData("test_ota_good_idempotent_downgrade")]
    [InlineData("test_ota_integrity_and_syntax_rejection")]
    [InlineData("test_ota_runtime_error_and_watchdog")]
    [InlineData("test_ota_restart_probation_and_catchup")]
    [InlineData("test_ota_storage_and_latest_wins")]
    [InlineData("test_ota_methods_and_timeout")]
    [InlineData("test_ota_download_local_http")]
    [InlineData("test_adr_chain_encodings")]
    [InlineData("test_adr_register_cache_renewal")]
    [InlineData("test_adr_errors_and_single_put")]
    [InlineData("test_adr_context_and_config")]
    [InlineData("test_adr_agent_hub_rejection")]
    [InlineData("test_adr_expiry_metadata")]
    [InlineData("test_log_levels_precedence_invalid")]
    [InlineData("test_log_rotation_caps")]
    [InlineData("test_log_failures_safe")]
    [InlineData("test_log_nonblocking_bounded_queue")]
    [InlineData("test_log_nested_redaction_console_file")]
    [InlineData("test_log_transport_trace_desired_changed")]
    public async Task InstalledPySharpRunsDeviceAssertions(string test)
    {
        var root = Environment.GetEnvironmentVariable("PYGATE_ROOT") ?? FindRepository();
        var scratch = Path.Combine(RecoveryRoot, "xunit", test, Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(scratch);
        var info = new ProcessStartInfo("pysharp")
        {
            WorkingDirectory = scratch,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        info.ArgumentList.Add("run");
        info.ArgumentList.Add(Path.Combine(root, "tests", "python", "run_tests.py"));
        info.ArgumentList.Add(test);
        info.Environment["PYGATE_TEST_SCRATCH"] = scratch;
        if (test == "test_mqtt_callbacks")
        {
            using var rsa = System.Security.Cryptography.RSA.Create(2048);
            var request = new System.Security.Cryptography.X509Certificates.CertificateRequest(
                "CN=offline-wrapper-test", rsa, System.Security.Cryptography.HashAlgorithmName.SHA256,
                System.Security.Cryptography.RSASignaturePadding.Pkcs1);
            using var certificate = request.CreateSelfSigned(DateTimeOffset.UtcNow.AddMinutes(-5), DateTimeOffset.UtcNow.AddDays(1));
            var certPath = Path.Combine(scratch, "client-cert.pem");
            var keyPath = Path.Combine(scratch, "client-key.pem");
            await File.WriteAllTextAsync(certPath, certificate.ExportCertificatePem());
            await File.WriteAllTextAsync(keyPath, rsa.ExportPkcs8PrivateKeyPem());
            info.Environment["PYGATE_TEST_CERT"] = certPath;
            info.Environment["PYGATE_TEST_KEY"] = keyPath;
            info.Environment["PYGATE_PAHO_SITE"] = Environment.GetEnvironmentVariable("PYGATE_PAHO_SITE")
                ?? @"D:\Configurations\AzureIoTForge\python-site" /* legacy stable paho site */;
        }
        if (test.StartsWith("test_adr_", StringComparison.Ordinal))
        {
            CreateAdrFixtures(scratch);
            info.Environment["PYGATE_ADR_FIXTURE_ROOT"] = scratch;
        }
        using var process = Process.Start(info) ?? throw new InvalidOperationException("Cannot start installed PySharp");
        var output = process.StandardOutput.ReadToEndAsync();
        var error = process.StandardError.ReadToEndAsync();
        // The virtual ten-minute test makes 602 real worker round trips (~100 ms each,
        // measured 58-70 s alone), so it gets a wider limit; every other case keeps 90 s.
        var limit = test == "test_ten_minutes_virtual_disconnect" ? 300 : 90;
        using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(limit));
        var contention = test == "test_worker_windows_contention" ? HoldWindowsWriter(scratch, timeout.Token) : Task.CompletedTask;
        try { await process.WaitForExitAsync(timeout.Token); await contention; }
        catch { if (!process.HasExited) process.Kill(true); throw; }
        var text = await output + await error;
        await File.WriteAllTextAsync(Path.Combine(scratch, "result.log"), text, new UTF8Encoding(false));
        var expectedExit = test == "test_reboot_process_exit" ? 75 : 0;
        Assert.True(process.ExitCode == expectedExit, text);
        Assert.Contains("PASS " + test, text);
    }

    private static async Task HoldWindowsWriter(string scratch, CancellationToken token)
    {
        Assert.True(OperatingSystem.IsWindows(), "This is an actual Windows sharing-violation test, not a mock.");
        var ready = Path.Combine(scratch, "contention-ready.json");
        while (!File.Exists(ready)) await Task.Delay(20, token);
        // The marker itself is not atomic; tolerate its short publication window.
        System.Text.Json.JsonDocument? document = null;
        while (document is null)
        {
            try { document = System.Text.Json.JsonDocument.Parse(await File.ReadAllTextAsync(ready, token)); }
            catch (System.Text.Json.JsonException) { await Task.Delay(20, token); }
            catch (IOException) { await Task.Delay(20, token); }
        }
        using (document)
        {
            var response = document.RootElement.GetProperty("response").GetString()!;
            var directory = document.RootElement.GetProperty("directory").GetString()!;
            var complete = await File.ReadAllBytesAsync(response, token);
            var readerLog = Path.Combine(directory, "reader.stderr.log");
            var baseline = new FileInfo(readerLog).Length;
            using (var writer = new FileStream(response, FileMode.Open, FileAccess.Write, FileShare.None))
            {
                writer.SetLength(0);
                await writer.WriteAsync(Encoding.UTF8.GetBytes("{\"id\":"), token);
                await writer.FlushAsync(token);
                await File.WriteAllTextAsync(Path.Combine(scratch, "contention-locked"), "windows-writer-held-open", token);
                // Prove the installed preview.8 reader hit the lock, not merely
                // that we held a file which nobody tried to open.
                while (new FileInfo(readerLog).Length <= baseline) await Task.Delay(20, token);
                await Task.Delay(1000, token);
            }
            await Task.Delay(1200, token); // Actual half-written JSON is now readable.
            var restoreDeadline = DateTime.UtcNow.AddSeconds(5);
            while (true)
            {
                try
                {
                    await File.WriteAllBytesAsync(response, complete, token);
                    break;
                }
                catch (IOException) when (DateTime.UtcNow < restoreDeadline)
                {
                    await Task.Delay(20, token);
                }
            }
            await File.WriteAllTextAsync(Path.Combine(scratch, "windows-contention-evidence.json"),
                System.Text.Json.JsonSerializer.Serialize(new { windowsWriterHeld = true, leakedReaderExceptionObserved = true, partialJsonDurationMs = 1200 }), token);
        }
    }

    [Fact]
    [Trait("Category", "Soak")]
    public async Task ThreeConcurrentLoopbackAgentsRunFor600RealSeconds()
    {
        var root = Environment.GetEnvironmentVariable("PYGATE_ROOT") ?? FindRepository();
        var run = Path.Combine(RecoveryRoot, "soak", DateTime.UtcNow.ToString("yyyyMMdd-HHmmss") + "-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(run);
        var started = DateTimeOffset.UtcNow;
        async Task<System.Text.Json.JsonElement> RunAgent(int index)
        {
            var scratch = Path.Combine(run, "agent-" + index);
            Directory.CreateDirectory(scratch);
            var info = new ProcessStartInfo("pysharp") { WorkingDirectory = scratch, RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false };
            info.ArgumentList.Add("run");
            info.ArgumentList.Add(Path.Combine(root, "tests", "python", "run_tests.py"));
            info.ArgumentList.Add("test_worker_loopback_soak");
            info.Environment["PYGATE_TEST_SCRATCH"] = scratch;
            info.Environment["PYGATE_SOAK_AGENT"] = "loopback-agent-" + index;
            using var process = Process.Start(info)!;
            var output = process.StandardOutput.ReadToEndAsync();
            var error = process.StandardError.ReadToEndAsync();
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(660));
            try { await process.WaitForExitAsync(timeout.Token); }
            catch { if (!process.HasExited) process.Kill(true); throw; }
            var text = await output + await error;
            await File.WriteAllTextAsync(Path.Combine(scratch, "result.log"), text);
            Assert.True(process.ExitCode == 0, text);
            Assert.Contains("PASS test_worker_loopback_soak", text);
            using var summary = System.Text.Json.JsonDocument.Parse(await File.ReadAllTextAsync(Path.Combine(scratch, "soak-summary.json")));
            Assert.True(summary.RootElement.GetProperty("elapsedSec").GetDouble() >= 600);
            Assert.Equal(0, summary.RootElement.GetProperty("payloadErrors").GetInt32());
            return summary.RootElement.Clone();
        }
        var results = await Task.WhenAll(Enumerable.Range(1, 3).Select(RunAgent));
        await File.WriteAllTextAsync(Path.Combine(run, "three-agent-summary.json"), System.Text.Json.JsonSerializer.Serialize(new { startedUtc = started, endedUtc = DateTimeOffset.UtcNow, concurrentAgents = 3, results }));
    }

    private static void CreateAdrFixtures(string folder)
    {
        using var rootKey = System.Security.Cryptography.ECDsa.Create(System.Security.Cryptography.ECCurve.NamedCurves.nistP384);
        using var issuerKey = System.Security.Cryptography.ECDsa.Create(System.Security.Cryptography.ECCurve.NamedCurves.nistP384);
        using var deviceKey = System.Security.Cryptography.ECDsa.Create(System.Security.Cryptography.ECCurve.NamedCurves.nistP384);
        using var wrongKey = System.Security.Cryptography.ECDsa.Create(System.Security.Cryptography.ECCurve.NamedCurves.nistP384);
        var rootRequest = new System.Security.Cryptography.X509Certificates.CertificateRequest("CN=offline-root", rootKey, System.Security.Cryptography.HashAlgorithmName.SHA384);
        rootRequest.CertificateExtensions.Add(new System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension(true, false, 0, true));
        using var root = rootRequest.CreateSelfSigned(DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddDays(100));
        var issuerRequest = new System.Security.Cryptography.X509Certificates.CertificateRequest("CN=offline-issuer", issuerKey, System.Security.Cryptography.HashAlgorithmName.SHA384);
        issuerRequest.CertificateExtensions.Add(new System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension(true, false, 0, true));
        using var issuerPublic = issuerRequest.Create(root, DateTimeOffset.UtcNow.AddHours(-1), DateTimeOffset.UtcNow.AddDays(90), System.Security.Cryptography.RandomNumberGenerator.GetBytes(16));
        using var issuer = System.Security.Cryptography.X509Certificates.ECDsaCertificateExtensions.CopyWithPrivateKey(issuerPublic, issuerKey);
        var deviceRequest = new System.Security.Cryptography.X509Certificates.CertificateRequest("CN=offline-test", deviceKey, System.Security.Cryptography.HashAlgorithmName.SHA384);
        using var leaf = deviceRequest.Create(issuer, DateTimeOffset.UtcNow.AddMinutes(-5), DateTimeOffset.UtcNow.AddDays(60), System.Security.Cryptography.RandomNumberGenerator.GetBytes(16));
        File.WriteAllText(Path.Combine(folder, "device-key.pem"), deviceKey.ExportPkcs8PrivateKeyPem());
        File.WriteAllText(Path.Combine(folder, "wrong-key.pem"), wrongKey.ExportPkcs8PrivateKeyPem());
        File.WriteAllText(Path.Combine(folder, "device.csr.pem"), deviceRequest.CreateSigningRequestPem());
        var leafPath = Path.Combine(folder, "fixture-leaf.pem");
        File.WriteAllText(leafPath, leaf.ExportCertificatePem());
        var expiryPath = Path.ChangeExtension(leafPath, ".expiry.json");
        File.WriteAllText(expiryPath, System.Text.Json.JsonSerializer.Serialize(new
        {
            notAfter = leaf.NotAfter.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", System.Globalization.CultureInfo.InvariantCulture),
            certificateSha256 = Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(leafPath)))
        }));
        using var expiry = System.Text.Json.JsonDocument.Parse(File.ReadAllText(expiryPath));
        Assert.Equal(leaf.NotAfter.ToUniversalTime().ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", System.Globalization.CultureInfo.InvariantCulture), expiry.RootElement.GetProperty("notAfter").GetString());
        Assert.Equal(Convert.ToHexStringLower(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(leafPath))), expiry.RootElement.GetProperty("certificateSha256").GetString());
        var certificates = new[] { issuerPublic, root, leaf }; // Deliberately not leaf-first.
        File.WriteAllText(Path.Combine(folder, "adr-fixture.json"), System.Text.Json.JsonSerializer.Serialize(new
        {
            pem = certificates.Select(c => c.ExportCertificatePem()).ToArray(),
            base64Pem = certificates.Select(c => Convert.ToBase64String(Encoding.UTF8.GetBytes(c.ExportCertificatePem()))).ToArray(),
            base64Der = certificates.Select(c => Convert.ToBase64String(c.RawData)).ToArray()
        }));
    }

    private static string FindRepository([System.Runtime.CompilerServices.CallerFilePath] string source = "")
    {
        for (var directory = new DirectoryInfo(Path.GetDirectoryName(source)!); directory is not null; directory = directory.Parent)
            if (File.Exists(Path.Combine(directory.FullName, "src", "AzureIoTPyGate.slnx"))) return directory.FullName;
        throw new InvalidOperationException("Set PYGATE_ROOT to the repository path.");
    }
}
