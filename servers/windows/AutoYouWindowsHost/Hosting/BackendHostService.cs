using AutoYouWindowsHost.Interop;
using System.ComponentModel;
using System.Diagnostics;
using System.Net.Http.Json;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.Json;

namespace AutoYouWindowsHost.Hosting;

internal sealed class BackendHostService : IDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private const string ShutdownTokenEnvironmentVariable = "AUTOYOU_SHUTDOWN_TOKEN";
    private const string ShutdownTokenHeader = "x-autoyou-shutdown-token";

    private readonly HttpClient httpClient;
    private readonly string appBaseDirectory;
    private readonly HostRuntimeConfiguration runtimeConfiguration;
    private readonly object backendLogSync = new();
    private readonly SemaphoreSlim startupSync = new(1, 1);
    private Process? ownedProcess;
    private nint ownedProcessJobHandle;
    private StreamWriter? backendStdOutWriter;
    private StreamWriter? backendStdErrWriter;
    private bool ownsProcess;
    private string? ownedProcessShutdownToken;

    public BackendHostService(string appBaseDirectory, HostRuntimeConfiguration runtimeConfiguration)
    {
        this.appBaseDirectory = appBaseDirectory;
        this.runtimeConfiguration = runtimeConfiguration;
        httpClient = new HttpClient
        {
            Timeout = TimeSpan.FromSeconds(2),
        };
        ActivePagePort = runtimeConfiguration.PagePort;
    }

    public int ActivePagePort { get; private set; }

    public string AdminUrl => runtimeConfiguration.AdminUrl;

    public string InstanceDisplayName => runtimeConfiguration.InstanceDisplayName;

    public string PortSummary => runtimeConfiguration.PortSummary;

    public async Task StartAsync(CancellationToken cancellationToken)
    {
        await startupSync.WaitAsync(cancellationToken).ConfigureAwait(false);

        try
        {
            ForgetExitedOwnedProcess();
            if (await IsServerReachableAsync(cancellationToken).ConfigureAwait(false))
            {
                return;
            }

            if (ownedProcess is not { HasExited: false })
            {
                var shutdownToken = Guid.NewGuid().ToString("N");
                var launchSpec = ResolveLaunchSpec(shutdownToken);
                CloseBackendLogWriters();

                var startInfo = new ProcessStartInfo
                {
                    FileName = launchSpec.FileName,
                    WorkingDirectory = launchSpec.WorkingDirectory,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8,
                };

                foreach (var argument in launchSpec.Arguments)
                {
                    startInfo.ArgumentList.Add(argument);
                }

                foreach (var environmentVariable in launchSpec.EnvironmentVariables)
                {
                    startInfo.Environment[environmentVariable.Key] = environmentVariable.Value;
                }

                ownedProcess = Process.Start(startInfo) ?? throw new InvalidOperationException("Unable to start the AutoYou backend process.");
                AttachOwnedProcessToJob(ownedProcess);
                AttachBackendLogCapture(ownedProcess);
                ownsProcess = true;
                ownedProcessShutdownToken = shutdownToken;
            }

            await WaitUntilReachableAsync(cancellationToken).ConfigureAwait(false);
        }
        finally
        {
            startupSync.Release();
        }
    }

    public async Task WaitUntilUrlReachableAsync(string url, CancellationToken cancellationToken)
    {
        var timeoutAt = DateTimeOffset.UtcNow.AddMinutes(2);
        while (DateTimeOffset.UtcNow < timeoutAt && !cancellationToken.IsCancellationRequested)
        {
            if (await IsUrlReachableAsync(url, cancellationToken).ConfigureAwait(false))
            {
                return;
            }

            ThrowIfOwnedBackendExited();
            await Task.Delay(TimeSpan.FromSeconds(1), cancellationToken).ConfigureAwait(false);
        }

        cancellationToken.ThrowIfCancellationRequested();
        throw new TimeoutException($"AutoYou did not make '{url}' reachable within the startup timeout.");
    }

    public async Task<bool> IsUrlReachableAsync(string url, CancellationToken cancellationToken)
    {
        try
        {
            using var response = await httpClient.GetAsync(url, cancellationToken).ConfigureAwait(false);
            return response.IsSuccessStatusCode;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch
        {
            return false;
        }
    }

    public async Task<BackendShellStatus> GetStatusAsync(CancellationToken cancellationToken)
    {
        try
        {
            var payload = await httpClient.GetFromJsonAsync<StartupStatusPayload>(
                new Uri(runtimeConfiguration.StatusUrl),
                JsonOptions,
                cancellationToken).ConfigureAwait(false);

            if (payload is not null)
            {
                if (payload.Instance?.Ports?.Page is { } pagePort && pagePort > 0)
                {
                    ActivePagePort = pagePort;
                }
                return InterpretStatus(payload, runtimeConfiguration.InstanceDisplayName);
            }
        }
        catch
        {
            // Fall through to coarse health checks.
        }

        if (await IsServerReachableAsync(cancellationToken).ConfigureAwait(false))
        {
            return BackendShellStatus.Running(runtimeConfiguration.InstanceDisplayName);
        }

        if (ownedProcess is { HasExited: false })
        {
            return BackendShellStatus.Starting(runtimeConfiguration.InstanceDisplayName);
        }

        return BackendShellStatus.Offline(runtimeConfiguration.InstanceDisplayName);
    }

    public async Task StopAsync(CancellationToken cancellationToken)
    {
        if (!ownsProcess)
        {
            return;
        }

        if (ownedProcess is null)
        {
            CloseOwnedProcessJob();
            return;
        }

        var gracefulShutdownRequested = false;
        var process = ownedProcess;

        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Post, new Uri(runtimeConfiguration.ShutdownUrl));
            if (!string.IsNullOrWhiteSpace(ownedProcessShutdownToken))
            {
                request.Headers.TryAddWithoutValidation(ShutdownTokenHeader, ownedProcessShutdownToken);
            }
            using var response = await httpClient.SendAsync(request, cancellationToken).ConfigureAwait(false);
            gracefulShutdownRequested = response.IsSuccessStatusCode;
            HostDiagnostics.LogInfo(
                $"Shutdown request for backend PID {process.Id} returned {(int)response.StatusCode} {response.ReasonPhrase}.");
        }
        catch (Exception ex)
        {
            HostDiagnostics.LogError($"Failed to request graceful shutdown for backend PID {process.Id}.", ex);
        }

        var exitedWithinTimeout = await WaitForProcessExitAsync(
            process,
            gracefulShutdownRequested ? TimeSpan.FromSeconds(25) : TimeSpan.FromSeconds(8)).ConfigureAwait(false);

        if (!exitedWithinTimeout)
        {
            HostDiagnostics.LogInfo(
                $"Backend PID {process.Id} did not exit within the allotted timeout. Closing the owned job object.");
            CloseOwnedProcessJob();
            exitedWithinTimeout = await WaitForProcessExitAsync(process, TimeSpan.FromSeconds(5)).ConfigureAwait(false);
        }

        if (!exitedWithinTimeout && !process.HasExited)
        {
            HostDiagnostics.LogInfo($"Backend PID {process.Id} still running after job close. Killing the process tree.");
            try
            {
                process.Kill(entireProcessTree: true);
            }
            catch (Exception ex)
            {
                HostDiagnostics.LogError($"Failed to kill backend PID {process.Id} after shutdown timeout.", ex);
            }

            _ = await WaitForProcessExitAsync(process, TimeSpan.FromSeconds(5)).ConfigureAwait(false);
        }

        if (process.HasExited)
        {
            HostDiagnostics.LogInfo($"Backend PID {process.Id} exited with code {process.ExitCode}.");
        }

        CloseOwnedProcessJob();
        CloseBackendLogWriters();
        ownsProcess = false;
        ownedProcessShutdownToken = null;
    }

    public void Dispose()
    {
        CloseOwnedProcessJob();
        CloseBackendLogWriters();
        startupSync.Dispose();
        httpClient.Dispose();
        ownedProcess?.Dispose();
        ownedProcessShutdownToken = null;
    }

    private async Task<bool> IsServerReachableAsync(CancellationToken cancellationToken)
    {
        try
        {
            using var response = await httpClient.GetAsync(new Uri(runtimeConfiguration.HealthUrl), cancellationToken).ConfigureAwait(false);
            return response.IsSuccessStatusCode;
        }
        catch
        {
            return false;
        }
    }

    private async Task WaitUntilReachableAsync(CancellationToken cancellationToken)
    {
        var timeoutAt = DateTimeOffset.UtcNow.AddMinutes(2);
        while (DateTimeOffset.UtcNow < timeoutAt && !cancellationToken.IsCancellationRequested)
        {
            if (await IsServerReachableAsync(cancellationToken).ConfigureAwait(false))
            {
                return;
            }

            ThrowIfOwnedBackendExited();
            await Task.Delay(TimeSpan.FromSeconds(1), cancellationToken).ConfigureAwait(false);
        }

        cancellationToken.ThrowIfCancellationRequested();
        throw new TimeoutException($"{runtimeConfiguration.InstanceDisplayName} did not make its health endpoint reachable within the startup timeout.");
    }

    private void ThrowIfOwnedBackendExited()
    {
        if (ownedProcess is { HasExited: true })
        {
            throw new InvalidOperationException($"The AutoYou backend exited early with code {ownedProcess.ExitCode}.");
        }
    }

    private void ForgetExitedOwnedProcess()
    {
        if (ownedProcess is not { HasExited: true } exitedProcess)
        {
            return;
        }

        try
        {
            HostDiagnostics.LogInfo($"Discarding exited backend PID {exitedProcess.Id} before restart (code {exitedProcess.ExitCode}).");
        }
        catch
        {
            // Best-effort diagnostics only.
        }

        CloseBackendLogWriters();
        ownedProcess = null;
        ownsProcess = false;
        ownedProcessShutdownToken = null;
        exitedProcess.Dispose();
    }

    private void AttachOwnedProcessToJob(Process process)
    {
        CloseOwnedProcessJob();

        var jobHandle = NativeMethods.CreateJobObject(nint.Zero, null);
        if (jobHandle == nint.Zero)
        {
            HostDiagnostics.LogError(
                $"CreateJobObject failed for backend PID {process.Id}.",
                new Win32Exception(Marshal.GetLastWin32Error()));
            return;
        }

        try
        {
            var limitInfo = new NativeMethods.JOBOBJECT_EXTENDED_LIMIT_INFORMATION
            {
                BasicLimitInformation = new NativeMethods.JOBOBJECT_BASIC_LIMIT_INFORMATION
                {
                    LimitFlags = NativeMethods.JobObjectLimitKillOnJobClose,
                },
            };

            if (!NativeMethods.SetInformationJobObject(
                    jobHandle,
                    NativeMethods.JobObjectExtendedLimitInformation,
                    ref limitInfo,
                    (uint)Marshal.SizeOf<NativeMethods.JOBOBJECT_EXTENDED_LIMIT_INFORMATION>()))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }

            if (!NativeMethods.AssignProcessToJobObject(jobHandle, process.Handle))
            {
                throw new Win32Exception(Marshal.GetLastWin32Error());
            }

            ownedProcessJobHandle = jobHandle;
            HostDiagnostics.LogInfo($"Attached backend PID {process.Id} to a Windows job object.");
        }
        catch (Exception ex)
        {
            _ = NativeMethods.CloseHandle(jobHandle);
            HostDiagnostics.LogError($"Failed to attach backend PID {process.Id} to a Windows job object.", ex);
        }
    }

    private async Task<bool> WaitForProcessExitAsync(Process process, TimeSpan timeout)
    {
        if (process.HasExited)
        {
            return true;
        }

        using var timeoutSource = new CancellationTokenSource(timeout);
        try
        {
            await process.WaitForExitAsync(timeoutSource.Token).ConfigureAwait(false);
            return true;
        }
        catch (OperationCanceledException)
        {
            return process.HasExited;
        }
    }

    private void CloseOwnedProcessJob()
    {
        if (ownedProcessJobHandle == nint.Zero)
        {
            return;
        }

        var jobHandle = ownedProcessJobHandle;
        ownedProcessJobHandle = nint.Zero;

        if (!NativeMethods.CloseHandle(jobHandle))
        {
            HostDiagnostics.LogError(
                "Failed to close the backend job object.",
                new Win32Exception(Marshal.GetLastWin32Error()));
        }
    }

    private LaunchSpec ResolveLaunchSpec(string shutdownToken)
    {
        var environmentVariables = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["AUTOYOU_INSTANCE_NAME"] = runtimeConfiguration.InstanceName,
            ["AUTOYOU_ADMIN_PORT"] = runtimeConfiguration.AdminPort.ToString(),
            ["AUTOYOU_AI_PORT"] = runtimeConfiguration.AiAgentPort.ToString(),
            ["AUTOYOU_AUTH_PORT"] = runtimeConfiguration.AuthPort.ToString(),
            ["ADMIN_WEB_SERVICE_PORT"] = runtimeConfiguration.AdminPort.ToString(),
            ["AI_AGENT_SERVER_PORT"] = runtimeConfiguration.AiAgentPort.ToString(),
            ["AUTH_SERVER_PORT"] = runtimeConfiguration.AuthPort.ToString(),
            [ShutdownTokenEnvironmentVariable] = shutdownToken,
            ["AUTOYOU_PARENT_PID"] = Environment.ProcessId.ToString(),
        };
        var explicitBindHost = Environment.GetEnvironmentVariable("AUTOYOU_BIND_HOST")
            ?? Environment.GetEnvironmentVariable("AUTOYOU_HOST_BIND");
        if (runtimeConfiguration.IsLite || !string.IsNullOrWhiteSpace(explicitBindHost))
        {
            environmentVariables["AUTOYOU_BIND_HOST"] = runtimeConfiguration.BindHost;
        }

        if (runtimeConfiguration.IsLite)
        {
            environmentVariables["AUTOYOU_LITE_AUTH_PORT"] = runtimeConfiguration.AuthPort.ToString();
        }

        var explicitBackend = Environment.GetEnvironmentVariable("AUTOYOU_BACKEND_EXE");
        if (!string.IsNullOrWhiteSpace(explicitBackend) && File.Exists(explicitBackend))
        {
            var backendDirectory = Path.GetDirectoryName(explicitBackend)!;
            AddPackagedRuntimeEnvironment(backendDirectory, environmentVariables, runtimeConfiguration.IsLite);
            return new LaunchSpec(explicitBackend, BuildLaunchArguments(), backendDirectory, environmentVariables);
        }

        var packagedBackend = ResolvePackagedBackendPath(appBaseDirectory);
        if (packagedBackend is not null)
        {
            var backendDirectory = Path.GetDirectoryName(packagedBackend)!;
            var workingDirectory = GetBackendUserDataDirectory();
            AddPackagedRuntimeEnvironment(backendDirectory, environmentVariables, runtimeConfiguration.IsLite);
            return new LaunchSpec(packagedBackend, BuildLaunchArguments(), workingDirectory, environmentVariables);
        }

        var repoRoot = FindRepoRoot(appBaseDirectory);
        if (repoRoot is not null)
        {
            var pythonExe = Path.Combine(repoRoot, ".venv", "Scripts", "python.exe");
            var scriptPath = Path.Combine(repoRoot, "server.py");
            if (File.Exists(pythonExe) && File.Exists(scriptPath))
            {
                var arguments = runtimeConfiguration.IsLite
                    ? new List<string> { "-m", "autoyou_lite.server" }
                    : new List<string> { scriptPath };
                arguments.AddRange(BuildLaunchArguments());
                return new LaunchSpec(
                    pythonExe,
                    arguments,
                    repoRoot,
                    environmentVariables);
            }
        }

        throw new FileNotFoundException(
            $"Unable to find a packaged {runtimeConfiguration.InstanceDisplayName} backend. Check the Backend folder or the local development fallback.");
    }

    private IReadOnlyList<string> BuildLaunchArguments()
    {
        if (runtimeConfiguration.IsLite)
        {
            return
            [
                "--host",
                runtimeConfiguration.BindHost,
                "--port",
                runtimeConfiguration.AdminPort.ToString(),
                "--auth-port",
                runtimeConfiguration.AuthPort.ToString(),
            ];
        }

        return
        [
            // Tell the Nuitka binary to go directly to server mode so it does
            // not also spin up a redundant pystray tray app alongside this host.
            "--run-server",
            "--admin",
            runtimeConfiguration.AdminPort.ToString(),
            "--ai-agent",
            runtimeConfiguration.AiAgentPort.ToString(),
            "--auth",
            runtimeConfiguration.AuthPort.ToString(),
        ];
    }

    private void AttachBackendLogCapture(Process process)
    {
        var stdoutPath = GetBackendStdOutLogPath();
        var stderrPath = GetBackendStdErrLogPath();

        backendStdOutWriter = CreateLogWriter(stdoutPath, "stdout");
        backendStdErrWriter = CreateLogWriter(stderrPath, "stderr");

        process.EnableRaisingEvents = true;
        process.OutputDataReceived += HandleBackendStdOut;
        process.ErrorDataReceived += HandleBackendStdErr;
        process.Exited += (_, _) =>
        {
            try
            {
                HostDiagnostics.LogInfo($"AutoYou backend exited with code {process.ExitCode}. Logs: '{stdoutPath}', '{stderrPath}'.");
            }
            catch
            {
                // Best-effort diagnostics only.
            }
        };

        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        HostDiagnostics.LogInfo($"Capturing backend stdout to '{stdoutPath}' and stderr to '{stderrPath}'.");
    }

    private void HandleBackendStdOut(object sender, DataReceivedEventArgs eventArgs)
    {
        WriteCapturedLogLine(backendStdOutWriter, eventArgs.Data);
    }

    private void HandleBackendStdErr(object sender, DataReceivedEventArgs eventArgs)
    {
        WriteCapturedLogLine(backendStdErrWriter, eventArgs.Data);
    }

    private void WriteCapturedLogLine(StreamWriter? writer, string? line)
    {
        if (writer is null || string.IsNullOrEmpty(line))
        {
            return;
        }

        lock (backendLogSync)
        {
            writer.WriteLine(line);
        }
    }

    private StreamWriter CreateLogWriter(string logPath, string streamLabel)
    {
        var writer = new StreamWriter(new FileStream(logPath, FileMode.Append, FileAccess.Write, FileShare.ReadWrite))
        {
            AutoFlush = true,
        };

        writer.WriteLine($"===== Backend session started {DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss zzz} ({streamLabel}; {runtimeConfiguration.PortSummary}) =====");
        return writer;
    }

    private string GetBackendStdOutLogPath()
    {
        return Path.Combine(GetBackendLogsDirectory(), BuildBackendLogFileName(isErrorLog: false));
    }

    private string GetBackendStdErrLogPath()
    {
        return Path.Combine(GetBackendLogsDirectory(), BuildBackendLogFileName(isErrorLog: true));
    }

    private string GetBackendLogsDirectory()
    {
        var logsDirectory = Path.Combine(GetBackendUserDataDirectory(), "logs");
        Directory.CreateDirectory(logsDirectory);
        return logsDirectory;
    }

    private string GetBackendUserDataDirectory()
    {
        var applicationData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
        var dataDirectory = runtimeConfiguration.IsLite
            ? Path.Combine(applicationData, runtimeConfiguration.Product.UserDataDirectoryName)
            : Path.Combine(applicationData, "AutoYou");
        Directory.CreateDirectory(dataDirectory);
        return dataDirectory;
    }

    private string BuildBackendLogFileName(bool isErrorLog)
    {
        if (runtimeConfiguration.InstanceName.Equals("default", StringComparison.OrdinalIgnoreCase)
            && runtimeConfiguration.AdminPort == 8001)
        {
            return isErrorLog ? "server_mode_error.log" : "server_mode.log";
        }

        var instanceSuffix = SanitizeFileNameComponent(runtimeConfiguration.InstanceName);
        var prefix = isErrorLog ? "server_mode_error" : "server_mode";
        return $"{prefix}.{instanceSuffix}.admin-{runtimeConfiguration.AdminPort}.log";
    }

    private static string SanitizeFileNameComponent(string rawValue)
    {
        if (string.IsNullOrWhiteSpace(rawValue))
        {
            return "default";
        }

        var invalidFileNameChars = Path.GetInvalidFileNameChars();
        var builder = new StringBuilder(rawValue.Length);

        foreach (var character in rawValue.Trim())
        {
            if (Array.IndexOf(invalidFileNameChars, character) >= 0)
            {
                builder.Append('-');
                continue;
            }

            builder.Append(char.IsWhiteSpace(character) ? '-' : char.ToLowerInvariant(character));
        }

        var normalized = builder.ToString().Trim('-');
        return string.IsNullOrWhiteSpace(normalized) ? "default" : normalized;
    }

    private void CloseBackendLogWriters()
    {
        lock (backendLogSync)
        {
            backendStdOutWriter?.Dispose();
            backendStdErrWriter?.Dispose();
            backendStdOutWriter = null;
            backendStdErrWriter = null;
        }
    }

    private static void AddPackagedRuntimeEnvironment(
        string backendDirectory,
        IDictionary<string, string> environmentVariables,
        bool isLite)
    {
        environmentVariables["AUTOYOU_PACKAGED_RUNTIME"] = "1";
        if (isLite)
        {
            environmentVariables["AUTOYOU_PACKAGED_RESOURCES_ROOT"] = backendDirectory;
        }

        var releaseMetadata = ReadReleaseProfileMetadata(backendDirectory);
        if (!string.IsNullOrWhiteSpace(releaseMetadata.ReleaseProfile))
        {
            environmentVariables["AUTOYOU_RELEASE_PROFILE"] = releaseMetadata.ReleaseProfile;
        }

        if (!string.IsNullOrWhiteSpace(releaseMetadata.DependencyProfile))
        {
            environmentVariables["AUTOYOU_DEPENDENCY_PROFILE"] = releaseMetadata.DependencyProfile;
        }

        if (string.Equals(releaseMetadata.ReleaseProfile, "binary-default", StringComparison.OrdinalIgnoreCase))
        {
            environmentVariables["AUTOYOU_ENABLE_TELEGRAM_PARTNER"] = "true";
            environmentVariables["AUTOYOU_ENABLE_SIGNAL_PARTNER"] = "false";
            environmentVariables["AUTOYOU_ENABLE_WHATSAPP_PARTNER"] = "false";
            environmentVariables["AUTOYOU_ENABLE_CLOUD_PROVIDER_CONNECTORS"] = "false";
        }

        var runtimeRoot = Path.Combine(backendDirectory, "runtime");
        var nodeExe = Path.Combine(runtimeRoot, "node", "node.exe");
        if (File.Exists(nodeExe))
        {
            environmentVariables["AUTOYOU_NODE_EXE"] = nodeExe;
        }

        var playwrightRoot = Path.Combine(runtimeRoot, "playwright");
        if (Directory.Exists(playwrightRoot))
        {
            environmentVariables["PLAYWRIGHT_BROWSERS_PATH"] = playwrightRoot;
        }

        var ollamaExe = Path.Combine(runtimeRoot, "ollama", "ollama.exe");
        if (File.Exists(ollamaExe))
        {
            environmentVariables["AUTOYOU_OLLAMA_EXE"] = ollamaExe;
        }

        var ollamaModelsRoot = Path.Combine(runtimeRoot, "ollama", "models");
        if (Directory.Exists(ollamaModelsRoot))
        {
            environmentVariables["OLLAMA_MODELS"] = ollamaModelsRoot;
        }

        var whisperModelsRoot = Path.Combine(runtimeRoot, "whisper", "models");
        if (Directory.Exists(whisperModelsRoot))
        {
            environmentVariables["AUTOYOU_WHISPER_MODELS_DIR"] = whisperModelsRoot;
        }
    }

    private static (string? ReleaseProfile, string? DependencyProfile) ReadReleaseProfileMetadata(string backendDirectory)
    {
        var metadataPath = Path.Combine(backendDirectory, "release-profile.json");
        if (!File.Exists(metadataPath))
        {
            return (null, null);
        }

        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(metadataPath));
            var root = document.RootElement;
            var releaseProfile = root.TryGetProperty("releaseProfile", out var releaseProfileElement)
                ? releaseProfileElement.GetString()
                : null;
            var dependencyProfile = root.TryGetProperty("dependencyProfile", out var dependencyProfileElement)
                ? dependencyProfileElement.GetString()
                : null;
            return (releaseProfile, dependencyProfile);
        }
        catch (Exception ex)
        {
            HostDiagnostics.LogError($"Failed to read release metadata from '{metadataPath}'.", ex);
            return (null, null);
        }
    }

    private string? ResolvePackagedBackendPath(string appBaseDirectory)
    {
        var backendDirectory = Path.Combine(appBaseDirectory, "Backend");
        var candidateNames = string.IsNullOrWhiteSpace(runtimeConfiguration.BackendExecutableName)
            ? new[] { "AutoYou.exe", "AutoYouServer.exe", "AutoYouDevServer.exe" }
            : new[] { runtimeConfiguration.BackendExecutableName };
        foreach (var candidateName in candidateNames)
        {
            var candidatePath = Path.Combine(backendDirectory, candidateName);
            if (File.Exists(candidatePath))
            {
                return candidatePath;
            }
        }

        return null;
    }

    private static string? FindRepoRoot(string startDirectory)
    {
        var directory = new DirectoryInfo(startDirectory);
        while (directory is not null)
        {
            if (File.Exists(Path.Combine(directory.FullName, "server.py")))
            {
                return directory.FullName;
            }

            directory = directory.Parent;
        }

        return null;
    }

    private static BackendShellStatus InterpretStatus(StartupStatusPayload payload, string productName)
    {
        var headline = payload.Headline?.Trim() ?? string.Empty;
        var normalizedStatus = payload.Status?.Trim() ?? string.Empty;

        if (headline.Contains("waiting for password", StringComparison.OrdinalIgnoreCase))
        {
            return BackendShellStatus.WaitingForPassword(productName);
        }

        if (payload.Initialized
            || string.Equals(normalizedStatus, "complete", StringComparison.OrdinalIgnoreCase)
            || string.Equals(normalizedStatus, "running", StringComparison.OrdinalIgnoreCase))
        {
            return BackendShellStatus.Running(productName);
        }

        if (string.Equals(normalizedStatus, "error", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(headline))
        {
            return new BackendShellStatus($"Status: {headline}", $"{productName}: {headline}");
        }

        return BackendShellStatus.Starting(productName);
    }

    private sealed record LaunchSpec(
        string FileName,
        IReadOnlyList<string> Arguments,
        string WorkingDirectory,
        IReadOnlyDictionary<string, string> EnvironmentVariables);

    private sealed class StartupStatusPayload
    {
        public string? Status { get; set; }

        public string? Headline { get; set; }

        public bool Initialized { get; set; }

        public InstancePayload? Instance { get; set; }
    }

    private sealed class InstancePayload
    {
        public PortsPayload? Ports { get; set; }
    }

    private sealed class PortsPayload
    {
        public int Admin { get; set; }
        public int Ai_Agent { get; set; }
        public int Auth { get; set; }
        public int Page { get; set; }
    }
}

internal sealed record BackendShellStatus(string MenuText, string TooltipText)
{
    public static BackendShellStatus Offline(string productName = "AutoYou") => new("Status: Offline", $"{productName} is offline");

    public static BackendShellStatus Starting(string productName = "AutoYou") => new(
        "Status: Initializing, please wait...",
        $"{productName} is initializing; the Admin UI will open when ready");

    public static BackendShellStatus Running(string productName = "AutoYou") => new("Status: Running", $"{productName} is running");

    public static BackendShellStatus WaitingForPassword(string productName = "AutoYou") => new("Status: Waiting for password", $"{productName} is waiting for the server password");
}
