(function () {
    "use strict";
    var root = new URL("./", window.location.href);
    function url(path) { return new URL(path.replace(/^\//, ""), root).href; }
    window.AutoYouContent.mount(document.getElementById("content-host"), {
        assetBase: url("/assets/"),
        url: url,
        request: function (path, options) { return fetch(url(path), Object.assign({ credentials: "same-origin" }, options)); },
        notice: function (kind, message) { document.getElementById("content-status").textContent = message; },
        navigate: function () { window.location.assign(url("/view")); }
    });
}());
