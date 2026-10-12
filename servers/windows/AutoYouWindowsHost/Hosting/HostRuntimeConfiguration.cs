using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;

namespace AutoYouWindowsHost.Hosting;

internal sealed record HostRuntimeConfiguration(
    HostProductProfile Product,
    string InstanceName,
    string BindHost,
    string ReachableHost,
    int AdminPort,
    int AiAgentPort,
    int AuthPort,
    int PagePort)
{
    public static HostRuntimeConfiguration Load()
    {
        var product = HostProductProfile.Load(AppContext.BaseDirectory);
        var instanceName = NormalizeInstanceName(
            Environment.GetEnvironmentVariable("AUTOYOU_INSTANCE_NAME"),
            product.DefaultInstanceName);
        var bindHost = NormalizeBindHost(Environment.GetEnvironmentVariable("AUTOYOU_BIND_HOST"));
        var adminPort = ReadPort("AUTOYOU_ADMIN_PORT", "ADMIN_WEB_SERVICE_PORT", product.DefaultAdminPort);
        var aiAgentPort = ReadPort("AUTOYOU_AI_PORT", "AI_AGENT_SERVER_PORT", product.DefaultAiAgentPort);
        var authPort = ReadPort("AUTOYOU_AUTH_PORT", "AUTH_SERVER_PORT", product.DefaultAuthPort);
        var pagePort = ReadPort("AUTOYOU_PAGE_PORT", "PAGE_SERVICE_PORT", product.DefaultPagePort);
        var reachableHost = NormalizeReachableHost(bindHost);

        return new HostRuntimeConfiguration(
            product,
            instanceName,
            bindHost,
            reachableHost,
            adminPort,
            aiAgentPort,
            authPort,
            pagePort);
    }

    public bool IsLite => Product.IsLite;

    public bool ShowSecondaryUi => Product.ShowSecondaryUi;

    public string BackendExecutableName => Product.BackendExecutableName;

    public string InstanceDisplayName => InstanceName.Equals(Product.DefaultInstanceName, StringComparison.OrdinalIgnoreCase)
        ? Product.DisplayName
        : $"{Product.DisplayName} [{InstanceName}]";

    public string AdminUrl => new UriBuilder(Uri.UriSchemeHttp, ReachableHost, AdminPort).Uri.AbsoluteUri;

    public string HealthUrl => BuildProductUrl(Product.HealthPath);

    public string StatusUrl => BuildProductUrl(Product.StatusPath);

    public string ShutdownUrl => BuildProductUrl(Product.ShutdownPath);

    public string AiAgentUrl
    {
        get
        {
            var builder = new UriBuilder(Uri.UriSchemeHttp, "localhost", AiAgentPort)
            {
                Path = "dev-ui/",
                Query = "app=autoyou_agents",
            };
            return builder.Uri.AbsoluteUri;
        }
    }

    public string InstanceKey
    {
        get
        {
            var rawValue = $"{Product.ProductId}|{ReachableHost}|{AdminPort}";
            var hash = SHA256.HashData(Encoding.UTF8.GetBytes(rawValue));
            return Convert.ToHexString(hash);
        }
    }

    public string PortSummary => IsLite
        ? $"admin {AdminPort}, auth {AuthPort}"
        : $"admin {AdminPort}, ai {AiAgentPort}, auth {AuthPort}";

    private string BuildProductUrl(string path)
    {
        var baseUri = new Uri(AdminUrl, UriKind.Absolute);
        return new Uri(baseUri, path.TrimStart('/')).AbsoluteUri;
    }

    private static string NormalizeInstanceName(string? rawValue, string defaultValue)
    {
        var candidate = (rawValue ?? string.Empty).Trim();
        return string.IsNullOrWhiteSpace(candidate) ? defaultValue : candidate;
    }

    private static string NormalizeBindHost(string? rawValue)
    {
        var candidate = (rawValue ?? string.Empty).Trim();
        return string.IsNullOrWhiteSpace(candidate) ? "127.0.0.1" : candidate;
    }

    private static string NormalizeReachableHost(string bindHost)
    {
        return bindHost switch
        {
            "0.0.0.0" or "::" => "127.0.0.1",
            _ => bindHost,
        };
    }

    private static int ReadPort(string primaryName, string legacyName, int defaultValue)
    {
        foreach (var variableName in new[] { primaryName, legacyName })
        {
            var rawValue = Environment.GetEnvironmentVariable(variableName);
            if (string.IsNullOrWhiteSpace(rawValue))
            {
                continue;
            }

            if (int.TryParse(rawValue, out var port) && port is > 0 and <= 65535)
            {
                return port;
            }
        }

        return defaultValue;
    }
}

internal sealed class HostInstanceLease : IDisposable
{
    private readonly Mutex mutex;
    private bool disposed;

    private HostInstanceLease(Mutex mutex, bool isPrimary)
    {
        this.mutex = mutex;
        IsPrimary = isPrimary;
    }

    public bool IsPrimary { get; }

    public static HostInstanceLease Acquire(HostRuntimeConfiguration configuration)
    {
        var mutexName = $@"Local\AutoYouWindowsHost-{configuration.InstanceKey}";
        var mutex = new Mutex(initiallyOwned: false, name: mutexName);
        var isPrimary = false;

        try
        {
            isPrimary = mutex.WaitOne(0, exitContext: false);
        }
        catch (AbandonedMutexException)
        {
            isPrimary = true;
        }

        return new HostInstanceLease(mutex, isPrimary);
    }

    public void Dispose()
    {
        if (disposed)
        {
            return;
        }

        if (IsPrimary)
        {
            try
            {
                mutex.ReleaseMutex();
            }
            catch (ApplicationException)
            {
                // Another shutdown path may have already released the mutex.
            }
        }

        mutex.Dispose();
        disposed = true;
    }
}

internal static class HostInstanceActivation
{
    public static void TryOpenAdminUi(HostRuntimeConfiguration configuration)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = configuration.AdminUrl,
                UseShellExecute = true,
            });
        }
        catch
        {
            // Best-effort activation of the already-running instance.
        }
    }
}
