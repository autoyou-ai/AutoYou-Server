using AutoYouWindowsHost.Hosting;
using System.Drawing;
using System.Runtime.InteropServices;

namespace AutoYouWindowsHost.Interop;

internal sealed class TrayIconController : IDisposable
{
    private const uint CallbackMessage = NativeMethods.WmApp + 1;
    private const nuint AdminUiCommandId = 1001;
    private const nuint AiAgentUiCommandId = 1002;
    private const nuint ExitCommandId = 1003;
    private const nuint AgentWebsitesCommandId = 1004;

    private readonly nint ownerHwnd;
    private readonly nint callbackHwnd;
    private readonly Action adminUiAction;
    private readonly Action aiAgentUiAction;
    private readonly Action agentWebsitesAction;
    private readonly Action exitAction;
    private readonly PngIconHandle? customIcon;
    private readonly string callbackWindowClassName = $"AutoYouTrayWindow.{Guid.NewGuid():N}";
    private readonly NativeMethods.WindowProc windowProcDelegate;

    private string tooltipText = "AutoYou Windows Host";
    private bool showAiAgentUi = false;
    private bool disposed;

    public TrayIconController(
        nint hwnd,
        string iconPath,
        Action adminUiAction,
        Action aiAgentUiAction,
        Action agentWebsitesAction,
        Action exitAction)
    {
        ownerHwnd = hwnd;
        this.adminUiAction = adminUiAction;
        this.aiAgentUiAction = aiAgentUiAction;
        this.agentWebsitesAction = agentWebsitesAction;
        this.exitAction = exitAction;

        customIcon = PngIconHandle.TryCreate(iconPath);
        windowProcDelegate = WindowProc;

        callbackHwnd = CreateCallbackWindow();
        if (callbackHwnd == nint.Zero)
        {
            throw new InvalidOperationException("Windows could not create the tray callback window.");
        }

        HostDiagnostics.LogInfo($"Tray callback window created. ownerHwnd={ownerHwnd}, callbackHwnd={callbackHwnd}.");

        AddOrUpdateIcon(NativeMethods.NimAdd);
    }

    private nint CreateCallbackWindow()
    {
        var moduleHandle = NativeMethods.GetModuleHandle(null);
        var windowClass = new NativeMethods.WNDCLASS
        {
            lpfnWndProc = Marshal.GetFunctionPointerForDelegate(windowProcDelegate),
            hInstance = moduleHandle,
            lpszClassName = callbackWindowClassName,
        };

        var classAtom = NativeMethods.RegisterClass(ref windowClass);
        if (classAtom == 0)
        {
            var errorCode = Marshal.GetLastWin32Error();
            if (errorCode != NativeMethods.ErrorClassAlreadyExists)
            {
                HostDiagnostics.LogError($"RegisterClass failed for tray callback window class '{callbackWindowClassName}' (Win32={errorCode}).");
                return nint.Zero;
            }
        }

        var handle = NativeMethods.CreateWindowEx(
            0,
            callbackWindowClassName,
            "AutoYou Tray Callback",
            0,
            0,
            0,
            0,
            0,
            nint.Zero,
            nint.Zero,
            moduleHandle,
            nint.Zero);

        if (handle == nint.Zero)
        {
            HostDiagnostics.LogError($"CreateWindowEx failed for tray callback window class '{callbackWindowClassName}' (Win32={Marshal.GetLastWin32Error()}).");
        }

        return handle;
    }

    public void UpdateStatus(string menuText, string tooltip, bool showAiAgentUi)
    {
        _ = menuText;
        tooltipText = tooltip;
        this.showAiAgentUi = showAiAgentUi;
        AddOrUpdateIcon(NativeMethods.NimModify);
    }

    public void Dispose()
    {
        if (disposed)
        {
            return;
        }

        var data = BuildNotifyIconData();
        _ = NativeMethods.Shell_NotifyIcon(NativeMethods.NimDelete, ref data);

        if (callbackHwnd != nint.Zero)
        {
            _ = NativeMethods.DestroyWindow(callbackHwnd);
        }

        customIcon?.Dispose();
        disposed = true;
    }

    private nint WindowProc(nint windowHandle, uint message, nuint wParam, nint lParam)
    {
        if (message == CallbackMessage)
        {
            // NOTIFYICON_VERSION_4 packs the icon ID into HIWORD(lParam).
            // Only LOWORD(lParam) is the actual tray notification code.
            var eventCode = ExtractTrayEventCode(lParam);
            if (eventCode == NativeMethods.WmLButtonDblClk)
            {
                HostDiagnostics.LogInfo("Tray icon double-click received.");
                adminUiAction();
                return nint.Zero;
            }

            if (eventCode == NativeMethods.WmRButtonUp || eventCode == NativeMethods.WmContextMenu)
            {
                HostDiagnostics.LogInfo($"Tray icon context menu event received (0x{eventCode:X}).");
                ShowContextMenu();
                return nint.Zero;
            }
        }

        return NativeMethods.DefWindowProc(windowHandle, message, wParam, lParam);
    }

    private void ShowContextMenu()
    {
        var menu = NativeMethods.CreatePopupMenu();
        if (menu == nint.Zero)
        {
            return;
        }

        try
        {
            _ = NativeMethods.AppendMenu(menu, NativeMethods.MfString, AdminUiCommandId, "Admin UI");
            if (showAiAgentUi)
            {
                _ = NativeMethods.AppendMenu(menu, NativeMethods.MfString, AiAgentUiCommandId, "AI Agent UI");
                _ = NativeMethods.AppendMenu(menu, NativeMethods.MfString, AgentWebsitesCommandId, "Agent Websites");
            }
            _ = NativeMethods.AppendMenu(menu, NativeMethods.MfString, ExitCommandId, "Exit");

            _ = NativeMethods.SetForegroundWindow(callbackHwnd);
            if (!NativeMethods.GetCursorPos(out var point))
            {
                return;
            }

            var command = NativeMethods.TrackPopupMenuEx(
                menu,
                NativeMethods.TpmBottomAlign | NativeMethods.TpmReturCmd | NativeMethods.TpmRightButton,
                point.X,
                point.Y,
                callbackHwnd,
                nint.Zero);

            _ = NativeMethods.PostMessage(callbackHwnd, NativeMethods.WmNull, nuint.Zero, nint.Zero);

            switch ((nuint)command)
            {
                case AdminUiCommandId:
                    HostDiagnostics.LogInfo("Tray context menu command selected: Admin UI.");
                    adminUiAction();
                    break;
                case AiAgentUiCommandId:
                    HostDiagnostics.LogInfo("Tray context menu command selected: AI Agent UI.");
                    aiAgentUiAction();
                    break;
                case AgentWebsitesCommandId:
                    HostDiagnostics.LogInfo("Tray context menu command selected: Agent Websites.");
                    agentWebsitesAction();
                    break;
                case ExitCommandId:
                    HostDiagnostics.LogInfo("Tray context menu command selected: Exit.");
                    exitAction();
                    break;
                default:
                    HostDiagnostics.LogInfo("Tray context menu dismissed without a selection.");
                    break;
            }
        }
        finally
        {
            _ = NativeMethods.DestroyMenu(menu);
        }
    }

    private void AddOrUpdateIcon(uint message)
    {
        var data = BuildNotifyIconData();
        var succeeded = NativeMethods.Shell_NotifyIcon(message, ref data);
        if (!succeeded && message == NativeMethods.NimAdd)
        {
            throw new InvalidOperationException("Windows refused to add the AutoYou tray icon.");
        }

        if (message == NativeMethods.NimAdd)
        {
            _ = NativeMethods.Shell_NotifyIcon(NativeMethods.NimSetVersion, ref data);
        }
    }

    private NativeMethods.NotifyIconData BuildNotifyIconData()
    {
        return new NativeMethods.NotifyIconData
        {
            cbSize = (uint)Marshal.SizeOf<NativeMethods.NotifyIconData>(),
            hWnd = callbackHwnd,
            uID = 1,
            uFlags = NativeMethods.NifIcon | NativeMethods.NifMessage | NativeMethods.NifTip,
            uCallbackMessage = CallbackMessage,
            hIcon = customIcon?.Handle ?? Icon.ExtractAssociatedIcon(Environment.ProcessPath!)?.Handle ?? SystemIcons.Application.Handle,
            szTip = Truncate(tooltipText, 127),
            szInfo = string.Empty,
            szInfoTitle = string.Empty,
            uVersion = NativeMethods.NotifyIconVersion4,
        };
    }

    private static string Truncate(string value, int maxLength)
        => value.Length <= maxLength ? value : value[..maxLength];

    private static uint ExtractTrayEventCode(nint lParam)
        => unchecked((uint)(ushort)lParam.ToInt64());
}
