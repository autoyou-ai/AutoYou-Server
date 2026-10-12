using System.Drawing;
using System.Runtime.InteropServices;

namespace AutoYouWindowsHost.Interop;

internal sealed class PngIconHandle : IDisposable
{
    private readonly nint iconHandle;
    private bool disposed;

    private PngIconHandle(Icon icon, nint iconHandle)
    {
        Icon = icon;
        this.iconHandle = iconHandle;
    }

    public Icon Icon { get; }

    public nint Handle => iconHandle;

    public static PngIconHandle? TryCreate(string iconPath)
    {
        if (!File.Exists(iconPath))
        {
            return null;
        }

        if (string.Equals(Path.GetExtension(iconPath), ".ico", StringComparison.OrdinalIgnoreCase))
        {
            // Load .ico directly - preserves all embedded sizes without resizing.
            var icon = new Icon(iconPath, 32, 32);
            return new PngIconHandle(icon, icon.Handle);
        }

        using var sourceBitmap = (Bitmap)System.Drawing.Image.FromFile(iconPath);
        using var resizedBitmap = new Bitmap(sourceBitmap, new Size(32, 32));
        var handle = resizedBitmap.GetHicon();
        return new PngIconHandle(Icon.FromHandle(handle), handle);
    }

    public void Dispose()
    {
        if (disposed)
        {
            return;
        }

        Icon.Dispose();
        _ = DestroyIcon(iconHandle);
        disposed = true;
    }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern bool DestroyIcon(nint hIcon);
}
