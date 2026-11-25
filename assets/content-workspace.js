(function () {
    "use strict";
    // The script is served from the API's assets directory. Its own address is
    // stable at the website root, /view, and behind either browser proxy.
    // document.baseURI belongs to the agent shell and must not resolve API URLs.
    var root = new URL("../", document.currentScript.src);
    var status = document.getElementById("content-status"), noticeTimer;
    // Standalone agent websites follow the device appearance. The shared screen
    // mounted inside Admin keeps Admin's own configured theme.
    var appearance = window.matchMedia("(prefers-color-scheme: dark)");
    function applyAppearance() {
        var theme = appearance.matches ? "dark" : "light";
        document.documentElement.setAttribute("data-theme", theme);
        document.documentElement.style.colorScheme = theme;
    }
    applyAppearance();
    appearance.addEventListener("change", applyAppearance);
    function url(path) {
        var target = new URL(path.replace(/^\//, ""), root);
        return target.pathname + target.search + target.hash;
    }
    window.AutoYouContent.mount(document.getElementById("content-host"), {
        assetBase: url("/assets/"),
        url: url,
        request: function (path, options) { return fetch(url(path), Object.assign({ credentials: "same-origin" }, options)); },
        notice: function (kind, message) {
            window.clearTimeout(noticeTimer);
            status.textContent = message;
            status.setAttribute("data-kind", kind);
            status.hidden = false;
            noticeTimer = window.setTimeout(function () { status.hidden = true; }, 7000);
        },
        navigate: function (screen, section) { window.location.assign(screen === "agents" ? "/websites" : "/agent/admin_agent/#" + encodeURIComponent(screen) + (section ? "/" + encodeURIComponent(section) : "")); }
    });
}());
