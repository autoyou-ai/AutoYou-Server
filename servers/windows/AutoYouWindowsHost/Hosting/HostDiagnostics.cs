namespace AutoYouWindowsHost.Hosting;

internal static class HostDiagnostics
{
    private static readonly object Sync = new();

    public static string GetLogPath()
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var logDirectory = Path.Combine(localAppData, "AutoYouWindowsHost", "logs");
        Directory.CreateDirectory(logDirectory);
        return Path.Combine(logDirectory, "host.log");
    }

    public static void LogInfo(string message)
    {
        WriteEntry("INFO", message);
    }

    public static void LogError(string message, Exception? exception = null)
    {
        var details = exception is null ? message : $"{message}{Environment.NewLine}{exception}";
        WriteEntry("ERROR", details);
    }

    private static void WriteEntry(string level, string message)
    {
        var line = $"[{DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss zzz}] {level} {message}{Environment.NewLine}";
        lock (Sync)
        {
            File.AppendAllText(GetLogPath(), line);
        }
    }
}
