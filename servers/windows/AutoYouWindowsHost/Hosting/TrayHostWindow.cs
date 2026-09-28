using AutoYouWindowsHost.Interop;
using System.Diagnostics;

namespace AutoYouWindowsHost.Hosting;

public sealed class TrayHostWindow : Window
{
    private readonly BackendHostService backendHostService;
    private readonly HostRuntimeConfiguration runtimeConfiguration;
    private readonly HostInstanceLease instanceLease;
    private readonly CancellationTokenSource lifetime = new();
    private TrayIconController? trayIconController;

    private Task? statusLoopTask;
    private bool exitRequested;
    private int startupBrowserOpened;
    private int exitStarted;

    internal TrayHostWindow(HostRuntimeConfiguration runtimeConfiguration, HostInstanceLease instanceLease)
    {
        this.runtimeConfiguration = runtimeConfiguration;
        this.instanceLease = instanceLease;

        Title = runtimeConfiguration.InstanceDisplayName;
        Content = new Grid();

        backendHostService = new BackendHostService(AppContext.BaseDirectory, runtimeConfiguration);

        Closed += OnClosed;
    }

    public async Task InitializeAsync()
    {
        HostDiagnostics.LogInfo($"Initializing {runtimeConfiguration.InstanceDisplayName} on {backendHostService.AdminUrl}");

        try
        {
            EnsureTrayIcon();
        }
        catch (Exception ex)
        {
            HostDiagnostics.LogError("Failed to initialize the tray icon.", ex);
            ShowStartupFailure($"{runtimeConfiguration.InstanceDisplayName} could not create its tray icon.{Environment.NewLine}{Environment.NewLine}{ex.Message}");
            return;
        }

        HideWindowToTray();
        ApplyStatus(BackendShellStatus.Starting(runtimeConfiguration.InstanceDisplayName));

        try
        {
            await backendHostService.StartAsync(lifetime.Token);
        }
        catch (Exception ex)
        {
            HostDiagnostics.LogError("The AutoYou backend exited during startup.", ex);
            ApplyStatus(new BackendShellStatus("Status: Error", $"{runtimeConfiguration.InstanceDisplayName} failed to start: {ex.Message}"));
        }

        await OpenUrlWhenReadyAsync(backendHostService.AdminUrl, markStartupBrowser: true);

        statusLoopTask ??= RunStatusLoopAsync(lifetime.Token);
    }

    private async Task RunStatusLoopAsync(CancellationToken cancellationToken)
    {
        while (!cancellationToken.IsCancellationRequested)
        {
            BackendShellStatus status;
            var showAiAgentUi = false;
            try
            {
                status = await backendHostService.GetStatusAsync(cancellationToken);
                if (runtimeConfiguration.ShowSecondaryUi
                    && string.Equals(status.MenuText, "Status: Running", StringComparison.OrdinalIgnoreCase))
                {
                    showAiAgentUi = await backendHostService.IsUrlReachableAsync(runtimeConfiguration.AiAgentUrl, cancellationToken);
                }
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception ex)
            {
                status = new BackendShellStatus("Status: Error", $"{runtimeConfiguration.InstanceDisplayName} status check failed: {ex.Message}");
            }

            if (status.MenuText.Equals("Status: Running", StringComparison.OrdinalIgnoreCase)
                && Interlocked.CompareExchange(ref startupBrowserOpened, 1, 0) == 0)
            {
                DispatcherQueue.TryEnqueue(() => OpenUrl(backendHostService.AdminUrl));
            }

            DispatcherQueue.TryEnqueue(() => ApplyStatus(status, showAiAgentUi));

            try
            {
                if (status.MenuText.Equals("Status: Offline", StringComparison.OrdinalIgnoreCase)
                    && !exitRequested)
                {
                    HostDiagnostics.LogInfo("Backend is offline; attempting an automatic restart.");
                    DispatcherQueue.TryEnqueue(() => ApplyStatus(BackendShellStatus.Starting(runtimeConfiguration.InstanceDisplayName)));
                    try
                    {
                        await backendHostService.StartAsync(cancellationToken);
                        status = await backendHostService.GetStatusAsync(cancellationToken);
                    }
                    catch (OperationCanceledException)
                    {
                        throw;
                    }
                    catch (Exception ex)
                    {
                        HostDiagnostics.LogError("Automatic backend restart failed; will retry.", ex);
                        status = BackendShellStatus.Starting(runtimeConfiguration.InstanceDisplayName);
                    }
                }

                await Task.Delay(TimeSpan.FromSeconds(3), cancellationToken);
            }
            catch (OperationCanceledException)
            {
                break;
            }
            catch (Exception ex)
            {
                HostDiagnostics.LogError("Backend status loop failed; will retry.", ex);
            }
        }
    }

    private void ApplyStatus(BackendShellStatus status, bool showAiAgentUi = false)
    {
        var menuText = $"{status.MenuText} ({runtimeConfiguration.AdminPort})";
        var tooltip = $"{backendHostService.InstanceDisplayName} [{backendHostService.PortSummary}] - {status.TooltipText}";
        trayIconController?.UpdateStatus(menuText, TruncateTrayText(tooltip), showAiAgentUi);
    }

    private Task OpenAdminUiAsync()
    {
        HostDiagnostics.LogInfo("Opening Admin UI from the tray icon.");
        return OpenUrlWhenReadyAsync(backendHostService.AdminUrl);
    }

    private Task OpenAiAgentUiAsync()
    {
        HostDiagnostics.LogInfo("Opening AI Agent UI from the tray icon.");
        return OpenSecondaryUrlOrAdminUiAsync(runtimeConfiguration.AiAgentUrl);
    }

    private Task OpenAgentWebsitesAsync()
    {
        HostDiagnostics.LogInfo("Opening Agent Websites from the tray icon.");
        int port = backendHostService.ActivePagePort;
        string url = $"http://{runtimeConfiguration.ReachableHost}:{port}/agent-websites";
        return OpenSecondaryUrlOrAdminUiAsync(url);
    }

    private async Task OpenUrlWhenReadyAsync(string url, bool markStartupBrowser = false)
    {
        try
        {
            ApplyStatus(BackendShellStatus.Starting(runtimeConfiguration.InstanceDisplayName));
            await backendHostService.StartAsync(lifetime.Token);
            await backendHostService.WaitUntilUrlReachableAsync(url, lifetime.Token);
            if (markStartupBrowser)
            {
                Interlocked.Exchange(ref startupBrowserOpened, 1);
            }
            OpenUrl(url);
        }
        catch (OperationCanceledException)
        {
            HostDiagnostics.LogInfo($"Opening '{url}' was canceled.");
        }
        catch (Exception ex)
        {
            HostDiagnostics.LogError($"Failed to open '{url}'.", ex);
            ApplyStatus(new BackendShellStatus("Status: Error", $"{runtimeConfiguration.InstanceDisplayName} failed to open: {ex.Message}"));
            ShowStartupFailure($"{runtimeConfiguration.InstanceDisplayName} could not open its local UI.{Environment.NewLine}{Environment.NewLine}{ex.Message}");
        }
    }

    private static void OpenUrl(string url)
    {
        Process.Start(new ProcessStartInfo
        {
            FileName = url,
            UseShellExecute = true,
        });
    }

    private async Task OpenSecondaryUrlOrAdminUiAsync(string url)
    {
        try
        {
            ApplyStatus(BackendShellStatus.Starting(runtimeConfiguration.InstanceDisplayName));
            await backendHostService.StartAsync(lifetime.Token);
            var targetUrl = await backendHostService.IsUrlReachableAsync(url, lifetime.Token)
                ? url
                : backendHostService.AdminUrl;
            if (!string.Equals(targetUrl, url, StringComparison.OrdinalIgnoreCase))
            {
                HostDiagnostics.LogInfo($"Secondary UI '{url}' is not reachable; opening Admin UI instead.");
            }

            OpenUrl(targetUrl);
        }
        catch (OperationCanceledException)
        {
            HostDiagnostics.LogInfo($"Opening secondary UI '{url}' was canceled.");
        }
        catch (Exception ex)
        {
            HostDiagnostics.LogError($"Failed to open secondary UI '{url}'.", ex);
            ApplyStatus(new BackendShellStatus("Status: Error", $"{runtimeConfiguration.InstanceDisplayName} failed to open: {ex.Message}"));
            OpenUrl(backendHostService.AdminUrl);
        }
    }

    private async Task ExitAsync()
    {
        if (Interlocked.Exchange(ref exitStarted, 1) != 0)
        {
            HostDiagnostics.LogInfo("Tray Exit requested while shutdown is already in progress.");
            return;
        }

        HostDiagnostics.LogInfo("Tray Exit requested. Stopping the owned backend.");
        exitRequested = true;
        ApplyStatus(new BackendShellStatus("Status: Stopping...", $"{runtimeConfiguration.InstanceDisplayName} is stopping"));
        lifetime.Cancel();

        try
        {
            await backendHostService.StopAsync(CancellationToken.None);
        }
        catch (Exception ex)
        {
            HostDiagnostics.LogError("Tray Exit encountered an error while stopping the backend.", ex);
        }
        finally
        {
            try
            {
                HostDiagnostics.LogInfo("Requesting tray host window shutdown on the UI thread.");
                await RunOnUiThreadAsync(() =>
                {
                    Close();
                    Application.Current.Exit();
                });
            }
            catch (Exception ex)
            {
                HostDiagnostics.LogError("Tray host UI-thread shutdown failed. Forcing process exit.", ex);
                Environment.Exit(0);
            }
        }
    }

    private Task RunOnUiThreadAsync(Action action)
    {
        if (DispatcherQueue.HasThreadAccess)
        {
            action();
            return Task.CompletedTask;
        }

        var completion = new TaskCompletionSource<object?>(TaskCreationOptions.RunContinuationsAsynchronously);
        var enqueued = DispatcherQueue.TryEnqueue(() =>
        {
            try
            {
                action();
                completion.TrySetResult(null);
            }
            catch (Exception ex)
            {
                completion.TrySetException(ex);
            }
        });

        if (!enqueued)
        {
            completion.TrySetException(new InvalidOperationException("Could not enqueue tray shutdown on the UI thread."));
        }

        return completion.Task;
    }

    private void HideWindowToTray()
    {
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
        NativeMethods.HideWindow(hwnd);
    }

    private void EnsureTrayIcon()
    {
        if (trayIconController is not null)
        {
            return;
        }

        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
        trayIconController = new TrayIconController(
            hwnd,
            ResolveTrayLogoPath(),
            () => _ = OpenAdminUiAsync(),
            () => _ = OpenAiAgentUiAsync(),
            () => _ = OpenAgentWebsitesAsync(),
            () => _ = ExitAsync());
    }

    private static string ResolveTrayLogoPath()
    {
        // Prefer .ico in packaged Assets, then repo assets - ICO carries all sizes natively.
        var packagedIco = Path.Combine(AppContext.BaseDirectory, "Assets", "logo.ico");
        if (File.Exists(packagedIco))
        {
            return packagedIco;
        }

        var repoRoot = FindRepoRoot(AppContext.BaseDirectory);
        if (repoRoot is not null)
        {
            var repoIco = Path.Combine(repoRoot, "assets", "logo.ico");
            if (File.Exists(repoIco))
            {
                return repoIco;
            }
        }

        throw new FileNotFoundException(
            "AutoYou requires Assets\\logo.ico in the published Windows host.",
            packagedIco);
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

    private static string TruncateTrayText(string value)
    {
        const int maxLength = 63;
        return value.Length <= maxLength ? value : value[..maxLength];
    }

    private void ShowStartupFailure(string message)
    {
        Title = $"{runtimeConfiguration.InstanceDisplayName} Startup Error";
        Content = new Grid
        {
            Children =
            {
                new TextBlock
                {
                    Margin = new Thickness(24),
                    Text = $"{message}{Environment.NewLine}{Environment.NewLine}Log: {HostDiagnostics.GetLogPath()}",
                    TextWrapping = TextWrapping.Wrap,
                }
            }
        };

        Activate();
    }

    private void OnClosed(object sender, WindowEventArgs args)
    {
        trayIconController?.Dispose();
        backendHostService.Dispose();
        instanceLease.Dispose();
        lifetime.Dispose();

        if (!exitRequested)
        {
            Application.Current.Exit();
        }
    }
}
