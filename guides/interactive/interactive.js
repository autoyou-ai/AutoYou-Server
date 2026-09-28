(function () {
    "use strict";

    var root = document.documentElement;
    var button = document.querySelector("[data-interactive-theme]");
    if (!button) return;

    function readTheme() {
        try {
            return window.localStorage.getItem("autoyou-theme");
        } catch (_) {
            return null;
        }
    }

    function writeTheme(theme) {
        try {
            window.localStorage.setItem("autoyou-theme", theme);
        } catch (_) {
            // Locked-down local previews can reject storage.
        }
    }

    function applyTheme(theme) {
        var normalized = theme === "light" ? "light" : "dark";
        root.setAttribute("data-theme", normalized);
        button.setAttribute("aria-pressed", normalized === "light" ? "true" : "false");
        button.textContent = normalized === "light" ? "Use dark mode" : "Use light mode";
    }

    var stored = readTheme();
    var preferred = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
    applyTheme(stored || preferred);
    button.addEventListener("click", function () {
        var next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
        applyTheme(next);
        writeTheme(next);
    });
}());
