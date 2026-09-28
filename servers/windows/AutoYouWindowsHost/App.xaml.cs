using AutoYouWindowsHost.Hosting;

namespace AutoYouWindowsHost
{
    /// <summary>
    /// Provides application-specific behavior to supplement the default Application class.
    /// </summary>
    public partial class App : Application
    {
        private TrayHostWindow? window;

        /// <summary>
        /// Initializes the singleton application object.  This is the first line of authored code
        /// executed, and as such is the logical equivalent of main() or WinMain().
        /// </summary>
        public App()
        {
            this.InitializeComponent();
        }

        /// <summary>
        /// Invoked when the application is launched normally by the end user.  Other entry points
        /// will be used such as when the application is launched to open a specific file.
        /// </summary>
        /// <param name="e">Details about the launch request and process.</param>
        protected override void OnLaunched(LaunchActivatedEventArgs e)
        {
            if (window is not null)
            {
                window.Activate();
                return;
            }

            var runtimeConfiguration = HostRuntimeConfiguration.Load();
            var instanceLease = HostInstanceLease.Acquire(runtimeConfiguration);
            if (!instanceLease.IsPrimary)
            {
                HostInstanceActivation.TryOpenAdminUi(runtimeConfiguration);
                instanceLease.Dispose();
                Exit();
                return;
            }

            window = new TrayHostWindow(runtimeConfiguration, instanceLease);
            window.Activate();
            _ = window.InitializeAsync();
        }
    }
}
