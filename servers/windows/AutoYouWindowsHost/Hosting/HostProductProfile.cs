using System.Text.Json;
using System.Text.Json.Serialization;

namespace AutoYouWindowsHost.Hosting;

internal sealed class HostProductProfile
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);

    public string ProductId { get; init; } = "autoyou";

    public string DisplayName { get; init; } = "AutoYou";

    public string DefaultInstanceName { get; init; } = "default";

    public string UserDataDirectoryName { get; init; } = "AutoYou";

    public string BackendExecutableName { get; init; } = "";

    public string HealthPath { get; init; } = "/api/status";

    public string StatusPath { get; init; } = "/api/login-startup-status";

    public string ShutdownPath { get; init; } = "/shutdown";

    public int DefaultAdminPort { get; init; } = 8001;

    public int DefaultAiAgentPort { get; init; } = 8081;

    public int DefaultAuthPort { get; init; } = 8002;

    public int DefaultPagePort { get; init; } = 8067;

    public bool ShowSecondaryUi { get; init; } = true;

    [JsonIgnore]
    public bool IsLite => ProductId.Equals("autoyou-lite", StringComparison.OrdinalIgnoreCase);

    public static HostProductProfile Load(string baseDirectory)
    {
        var profilePath = Path.Combine(baseDirectory, "host-profile.json");
        if (!File.Exists(profilePath))
        {
            return new HostProductProfile();
        }

        var profile = JsonSerializer.Deserialize<HostProductProfile>(
            File.ReadAllText(profilePath),
            JsonOptions);
        if (profile is null)
        {
            throw new InvalidDataException($"Windows host profile '{profilePath}' is empty.");
        }

        profile.Validate(profilePath);
        return profile;
    }

    private void Validate(string profilePath)
    {
        if (string.IsNullOrWhiteSpace(ProductId) || string.IsNullOrWhiteSpace(DisplayName))
        {
            throw new InvalidDataException($"Windows host profile '{profilePath}' must define ProductId and DisplayName.");
        }

        if (!string.IsNullOrWhiteSpace(BackendExecutableName)
            && !string.Equals(Path.GetFileName(BackendExecutableName), BackendExecutableName, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"Windows host profile '{profilePath}' must use a backend executable file name, not a path.");
        }

        if (string.IsNullOrWhiteSpace(UserDataDirectoryName)
            || !string.Equals(Path.GetFileName(UserDataDirectoryName), UserDataDirectoryName, StringComparison.Ordinal))
        {
            throw new InvalidDataException($"Windows host profile '{profilePath}' has an invalid user data directory name.");
        }

        ValidateRoute(HealthPath, nameof(HealthPath), profilePath);
        ValidateRoute(StatusPath, nameof(StatusPath), profilePath);
        ValidateRoute(ShutdownPath, nameof(ShutdownPath), profilePath);

        ValidatePort(DefaultAdminPort, nameof(DefaultAdminPort), profilePath, allowZero: false);
        ValidatePort(DefaultAiAgentPort, nameof(DefaultAiAgentPort), profilePath, allowZero: true);
        ValidatePort(DefaultAuthPort, nameof(DefaultAuthPort), profilePath, allowZero: false);
        ValidatePort(DefaultPagePort, nameof(DefaultPagePort), profilePath, allowZero: true);
    }

    private static void ValidateRoute(string value, string propertyName, string profilePath)
    {
        if (string.IsNullOrWhiteSpace(value) || !value.StartsWith("/", StringComparison.Ordinal) || value.Contains(':'))
        {
            throw new InvalidDataException($"Windows host profile '{profilePath}' has an invalid {propertyName}.");
        }
    }

    private static void ValidatePort(int value, string propertyName, string profilePath, bool allowZero)
    {
        var minimum = allowZero ? 0 : 1;
        if (value < minimum || value > 65535)
        {
            throw new InvalidDataException($"Windows host profile '{profilePath}' has an invalid {propertyName}.");
        }
    }
}
