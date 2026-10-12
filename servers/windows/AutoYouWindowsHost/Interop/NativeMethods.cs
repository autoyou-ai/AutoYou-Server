using System.Runtime.InteropServices;

namespace AutoYouWindowsHost.Interop;

internal static class NativeMethods
{
    public const int GwlWndProc = -4;
    public const int ErrorClassAlreadyExists = 1410;
    public const int JobObjectExtendedLimitInformation = 9;
    public const uint JobObjectLimitKillOnJobClose = 0x00002000;
    public const uint MfDisabled = 0x00000002;
    public const uint MfGray = 0x00000001;
    public const uint MfString = 0x00000000;
    public const uint NifIcon = 0x00000002;
    public const uint NifMessage = 0x00000001;
    public const uint NifTip = 0x00000004;
    public const uint NimAdd = 0x00000000;
    public const uint NimDelete = 0x00000002;
    public const uint NimModify = 0x00000001;
    public const uint NimSetVersion = 0x00000004;
    public const uint NotifyIconVersion4 = 4;
    private const int SwHide = 0;
    public const uint TpmBottomAlign = 0x0020;
    public const uint TpmReturCmd = 0x0100;
    public const uint TpmRightButton = 0x0002;
    public const uint WmApp = 0x8000;
    public const uint WmContextMenu = 0x007B;
    public const uint WmLButtonDblClk = 0x0203;
    public const uint WmNull = 0x0000;
    public const uint WmRButtonUp = 0x0205;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct WNDCLASS
    {
        public uint style;
        public nint lpfnWndProc;
        public int cbClsExtra;
        public int cbWndExtra;
        public nint hInstance;
        public nint hIcon;
        public nint hCursor;
        public nint hbrBackground;
        public string? lpszMenuName;
        public string lpszClassName;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct NotifyIconData
    {
        public uint cbSize;
        public nint hWnd;
        public uint uID;
        public uint uFlags;
        public uint uCallbackMessage;
        public nint hIcon;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)]
        public string szTip;

        public uint dwState;
        public uint dwStateMask;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)]
        public string szInfo;

        public uint uVersion;

        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)]
        public string szInfoTitle;

        public uint dwInfoFlags;
        public Guid guidItem;
        public nint hBalloonIcon;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct Point
    {
        public int X;
        public int Y;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public nuint MinimumWorkingSetSize;
        public nuint MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public nuint Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct IO_COUNTERS
    {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public nuint ProcessMemoryLimit;
        public nuint JobMemoryLimit;
        public nuint PeakProcessMemoryUsed;
        public nuint PeakJobMemoryUsed;
    }

    public delegate nint WindowProc(nint hwnd, uint msg, nuint wParam, nint lParam);

    public static void HideWindow(nint hwnd)
    {
        if (hwnd != nint.Zero)
        {
            _ = ShowWindow(hwnd, SwHide);
        }
    }

    public static nint SetWindowLongPtr(nint hWnd, int nIndex, nint dwNewLong)
        => IntPtr.Size == 8
            ? SetWindowLongPtr64(hWnd, nIndex, dwNewLong)
            : SetWindowLong32(hWnd, nIndex, dwNewLong);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern bool Shell_NotifyIcon(uint dwMessage, ref NotifyIconData lpData);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern nint CreatePopupMenu();

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern bool AppendMenu(nint hMenu, uint uFlags, nuint uIDNewItem, string lpNewItem);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool DestroyMenu(nint hMenu);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool GetCursorPos(out Point lpPoint);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool SetForegroundWindow(nint hWnd);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern uint TrackPopupMenuEx(
        nint hmenu,
        uint fuFlags,
        int x,
        int y,
        nint hwnd,
        nint lptpm);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern ushort RegisterClass(ref WNDCLASS lpWndClass);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern nint CreateWindowEx(
        uint dwExStyle,
        string lpClassName,
        string lpWindowName,
        uint dwStyle,
        int x,
        int y,
        int nWidth,
        int nHeight,
        nint hWndParent,
        nint hMenu,
        nint hInstance,
        nint lpParam);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool DestroyWindow(nint hWnd);

    [DllImport("user32.dll")]
    public static extern nint CallWindowProc(nint lpPrevWndFunc, nint hWnd, uint msg, nuint wParam, nint lParam);

    [DllImport("user32.dll")]
    public static extern nint DefWindowProc(nint hWnd, uint msg, nuint wParam, nint lParam);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool PostMessage(nint hWnd, uint msg, nuint wParam, nint lParam);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(nint hWnd, int nCmdShow);

    [DllImport("user32.dll", EntryPoint = "SetWindowLong")]
    private static extern nint SetWindowLong32(nint hWnd, int nIndex, nint dwNewLong);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtr")]
    private static extern nint SetWindowLongPtr64(nint hWnd, int nIndex, nint dwNewLong);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern nint GetModuleHandle(string? lpModuleName);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern nint CreateJobObject(nint lpJobAttributes, string? lpName);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetInformationJobObject(
        nint hJob,
        int jobObjectInfoClass,
        ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION lpJobObjectInfo,
        uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool AssignProcessToJobObject(nint job, nint process);

    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool CloseHandle(nint handle);
}
