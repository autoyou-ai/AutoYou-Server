/* My Content screen for the admin console.
 *
 * Mounted by admin-ui.js into the My Content screen. It keeps its own state and DOM
 * across the shell's re-renders (detach/attach), asks the server only for what
 * changed (ETag summary, then the change feed), and answers search, filters and
 * grouping from the cached index in content-store.js. Data is only ever put in the
 * page with textContent or attributes, never as markup.
 *
 *   AutoYouContent.mount(host, ctx)   show the screen in `host`
 *   AutoYouContent.detach()           the shell is about to replace its DOM
 *   AutoYouContent.unmount()          the screen was left
 *
 * ctx: { request(path, options) -> Promise<Response>, url(path) -> string, assetBase,
 *        notice(kind, message), navigate(screenId) }  - paths are relative to the content API.
 */
(function () {
    "use strict";

    var Store = window.AutoYouContentStore;
    var NS = "http://www.w3.org/2000/svg";
    var PAGE = 60;
    var ICONS = {
        note: "M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8",
        page: "M3 5h18a1 1 0 011 1v12a1 1 0 01-1 1H3a1 1 0 01-1-1V6a1 1 0 011-1zM1 10h22",
        image: "M21 15V5a2 2 0 00-2-2H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2zM8.5 10a1.5 1.5 0 100-3 1.5 1.5 0 000 3zM21 15l-5-5L5 21",
        audio: "M9 18V5l12-2v13M9 18a3 3 0 11-6 0 3 3 0 016 0zM21 16a3 3 0 11-6 0 3 3 0 016 0z",
        video: "M23 7l-7 5 7 5V7zM1 5h15v14H1z",
        file: "M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9zM13 2v7h7",
        voice: "M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3zM19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8",
        location: "M21 10c0 7-9 13-9 13S3 17 3 10a9 9 0 0118 0zM12 13a3 3 0 100-6 3 3 0 000 6z",
        pin: "M21 10c0 7-9 13-9 13S3 17 3 10a9 9 0 0118 0zM12 13a3 3 0 100-6 3 3 0 000 6z",
        book: "M4 19.5A2.5 2.5 0 016.5 17H20M4 19.5A2.5 2.5 0 004 17V5a2 2 0 012-2h12a2 2 0 012 2v14H6.5A2.5 2.5 0 014 19.5z",
        game: "M6 12h4M8 10v4M15 13h.01M18 11h.01M17.32 5H6.68a4 4 0 00-3.98 3.59L2 17a3 3 0 005.12 2.12L9 17h6l1.88 2.12A3 3 0 0022 17l-.7-8.41A4 4 0 0017.32 5z",
        bell: "M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 01-3.46 0",
        folder: "M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z",
        spark: "M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9zM19 15l.7 1.8 1.8.7-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7z",
        agent: "M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16zM3.27 6.96L12 12.01l8.73-5.05M12 22.08V12",
        search: "M21 21l-4.35-4.35M17 11A6 6 0 115 11a6 6 0 0112 0z",
        plus: "M12 5v14M5 12h14",
        refresh: "M23 4v6h-6M1 20v-6h6M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15",
        sliders: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6",
        download: "M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3",
        trash: "M3 6h18M8 6V4h8v2M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6M10 11v6M14 11v6",
        external: "M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3",
        close: "M18 6L6 18M6 6l12 12",
        check: "M20 6L9 17l-5-5",
        right: "M9 18l6-6-6-6",
        down: "M6 9l6 6 6-6",
        grid: "M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z",
        list: "M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01",
        info: "M12 22a10 10 0 100-20 10 10 0 000 20zM12 16v-4M12 8h.01",
        alert: "M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0zM12 9v4M12 17h.01",
        upload: "M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M17 8l-5-5-5 5M12 3v12",
        lock: "M19 11H5a2 2 0 00-2 2v7a2 2 0 002 2h14a2 2 0 002-2v-7a2 2 0 00-2-2zM7 11V7a5 5 0 0110 0v4",
        tag: "M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82zM7 7h.01"
    };
    var STATES = {
        pending: "Organizing soon", waiting_model: "Waiting for the local AI", failed: "Could not be organized yet",
        missing: "File not found in its folder", ready: ""
    };

    var S = null; // the one screen instance, created on first mount and kept

    // ---- small DOM helpers ------------------------------------------------------------------------
    function append(parent, child) {
        if (child == null || child === false) { return; }
        if (Array.isArray(child)) { child.forEach(function (item) { append(parent, item); }); return; }
        parent.appendChild(typeof child === "object" ? child : document.createTextNode(String(child)));
    }
    function h(tag, attrs) {
        var el = document.createElement(tag);
        if (attrs) {
            Object.keys(attrs).forEach(function (key) {
                var value = attrs[key];
                if (value == null || value === false) { return; }
                if ((key === "href" || key === "src") && typeof value === "string") { value = Store.safeUrl(value); if (!value) { return; } }
                if (key === "class") { el.className = value; }
                else if (key === "text") { el.textContent = value; }
                else if (key.slice(0, 2) === "on") { el.addEventListener(key.slice(2), value); }
                else if (value === true) { el.setAttribute(key, ""); }
                else { el.setAttribute(key, value); }
            });
        }
        for (var i = 2; i < arguments.length; i += 1) { append(el, arguments[i]); }
        return el;
    }
    function icon(name, className) {
        var svg = document.createElementNS(NS, "svg");
        svg.setAttribute("viewBox", "0 0 24 24");
        svg.setAttribute("fill", "none");
        svg.setAttribute("stroke", "currentColor");
        svg.setAttribute("stroke-width", "1.8");
        svg.setAttribute("stroke-linecap", "round");
        svg.setAttribute("stroke-linejoin", "round");
        svg.setAttribute("aria-hidden", "true");
        svg.setAttribute("class", "ayc-icon" + (className ? " " + className : ""));
        var path = document.createElementNS(NS, "path");
        path.setAttribute("d", ICONS[name] || ICONS.file);
        svg.appendChild(path);
        return svg;
    }
    function button(label, options) {
        options = options || {};
        var el = h("button", {
            type: "button", class: "ayu-btn ayu-btn-" + (options.variant || "secondary") + (options.small ? " ayu-btn-sm" : "") + (options.extra ? " " + options.extra : ""),
            "data-act": options.act, "data-id": options.id, "aria-label": options.label, title: options.title, disabled: options.disabled
        }, options.icon ? icon(options.icon) : null, options.iconOnly ? null : h("span", { text: label }));
        return el;
    }
    function iconButton(name, label, act, id, extra) {
        return h("button", { type: "button", class: "ayc-iconbtn" + (extra ? " " + extra : ""), "data-act": act, "data-id": id, "aria-label": label, title: label }, icon(name));
    }
    function clear(el) { while (el.firstChild) { el.removeChild(el.firstChild); } }
    function plural(count, one, many) { return count + " " + (count === 1 ? one : (many || one + "s")); }
    function kindLabel(kind) { return Store.KIND_LABEL[kind] || kind; }
    function singular(kind) { return { note: "Note", page: "Page", image: "Image", video: "Video", audio: "Audio", voice: "Voice", file: "File", location: "Location", agent: "Record" }[kind] || kind; }
    function kindIcon(kind) { return ICONS[kind] ? kind : "file"; }
    function stateText(row) { return STATES[row.organization_state] || ""; }

    // ---- network ------------------------------------------------------------------------------------
    function api(path, options) {
        options = options || {};
        return S.ctx.request(path, options).then(function (response) {
            if (response.status === 304) { return { notModified: true }; }
            return response.json().catch(function () { return null; }).then(function (body) {
                if (!response.ok) {
                    var detail = body && (typeof body.error === "string" ? body.error : typeof body.detail === "string" ? body.detail : "");
                    var error = new Error(detail || (response.status === 401 ? "Sign in again to continue." : response.status === 403 ? "This account is not allowed to do that." : "The computer did not accept that request."));
                    error.status = response.status;
                    error.body = body;
                    throw error;
                }
                return { body: body, etag: response.headers && response.headers.get ? response.headers.get("ETag") || "" : "" };
            });
        });
    }
    function json(method, body) {
        return { method: method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    }
    function notify(kind, message) {
        say(message);
        if (S.ctx && S.ctx.notice) { S.ctx.notice(kind, message); }
    }
    function say(message) {
        if (!S || !S.live) { return; }
        S.live.textContent = "";
        window.setTimeout(function () { if (S && S.live) { S.live.textContent = message; } }, 30);
    }

    // ---- state --------------------------------------------------------------------------------------
    function create() {
        S = {
            store: Store.createStore(), ctx: null, root: null, layer: null, parking: null, host: null, mounted: false,
            filter: { text: "", kind: "", source: "", collection: "", sort: "updated" },
            view: "agents", limit: PAGE, load: "loading", error: "", offline: false,
            selected: new Set(), lastPicked: "", active: "", preview: null, previewState: "idle", previewToken: 0,
            etag: "", groups: null, groupsKey: "", server: null, serverKey: "", serverToken: 0,
            walking: false, walkToken: 0, walkError: "", poll: { timer: 0, wait: 5000, floor: 5000, ceiling: 60000 }, uploads: [], uploading: false, syncing: false,
            sigs: {}, scheduled: false, thumbs: new Map(), thumbFail: new Set(), thumbQueue: [], thumbBusy: 0, observer: null,
            started: false, polling: false, memos: {}, restoreFocus: null, ro: null, wide: window.matchMedia ? window.matchMedia("(min-width: 1180px)") : { matches: true, addEventListener: function () {} }
        };
        build();
        return S;
    }

    function unreachable(error) { return !error.status || error.status >= 500; }
    function can(action) { return !!(S.store.summary && S.store.summary.abilities && S.store.summary.abilities[action]); }
    function agentOf(source) { return S.store.agent(source); }
    function hasFilter() { var f = S.filter; return !!(f.text.trim() || f.kind || f.source || f.collection); }
    function overview() { return S.view === "agents" && !hasFilter(); }

    function schedule() {
        if (S.scheduled || !S.mounted) { return; }
        S.scheduled = true;
        (window.requestAnimationFrame || window.setTimeout)(function () { S.scheduled = false; if (S.mounted) { renderAll(); } });
    }
    function once(key, signature, draw) {
        if (S.sigs[key] === signature) { return false; }
        S.sigs[key] = signature;
        draw();
        return true;
    }

    // ---- structure ----------------------------------------------------------------------------------
    function build() {
        var s = S;
        s.live = h("div", { class: "ayu-sr-only", role: "status", "aria-live": "polite" });
        s.title = h("h1", { text: "My Content" });
        s.sub = h("p", { class: "ayc-sub" });
        s.actions = h("div", { class: "ayu-inline-actions ayc-actions" });
        s.banner = h("div", { class: "ayc-banners" });
        s.search = h("input", {
            type: "search", class: "ayc-search-input", placeholder: "Search titles, labels, notes and agents", maxlength: "300",
            "aria-label": "Search My Content", autocomplete: "off", spellcheck: "false", "data-ayc": "search"
        });
        s.searchClear = iconButton("close", "Clear search", "clear-search", "", "ayc-search-clear");
        s.agentSelect = h("select", { class: "ayu-select ayc-select", "aria-label": "Agent", "data-ayc": "source" });
        s.collectionSelect = h("select", { class: "ayu-select ayc-select", "aria-label": "Collection", "data-ayc": "collection" });
        s.sortSelect = h("select", { class: "ayu-select ayc-select", "aria-label": "Sort by", "data-ayc": "sort" },
            h("option", { value: "updated", text: "Newest first" }), h("option", { value: "title", text: "Name" }), h("option", { value: "size", text: "Largest first" }));
        s.viewToggle = h("div", { class: "ayc-seg", role: "group", "aria-label": "View" },
            h("button", { type: "button", "data-act": "view", "data-id": "agents", "aria-pressed": "true", title: "Group by agent" }, icon("grid"), h("span", { text: "By agent" })),
            h("button", { type: "button", "data-act": "view", "data-id": "list", "aria-pressed": "false", title: "Show one list" }, icon("list"), h("span", { text: "List" })));
        s.kinds = h("div", { class: "ayc-chips", role: "group", "aria-label": "Type" });
        s.agentRail = h("div", { class: "ayc-chips ayc-agentrail", role: "group", "aria-label": "Agent" });
        s.results = h("div", { class: "ayc-results", "data-ayc": "results" });
        s.resultsPanel = h("section", { class: "ayu-panel ayc-panel", "aria-label": "Content" },
            h("div", { class: "ayu-panel-head ayc-panel-head" }, s.panelHead = h("div")), s.results);
        s.previewBody = h("div", { class: "ayc-preview-body" });
        s.preview_el = h("aside", { class: "ayu-panel ayc-preview", "aria-label": "Item details", hidden: true, tabindex: "-1" }, s.previewBody);
        s.main = h("div", { class: "ayc-main" }, s.resultsPanel, s.preview_el);
        s.bulk = h("div", { class: "ayc-bulk", hidden: true, role: "region", "aria-label": "Selected items" });
        s.uploadPanel = h("div", { class: "ayc-uploads", hidden: true, role: "region", "aria-label": "Adding files" });
        s.fileInput = h("input", { type: "file", multiple: true, class: "ayc-file", tabindex: "-1", "aria-hidden": "true" });
        s.folderInput = h("input", { type: "file", multiple: true, webkitdirectory: true, class: "ayc-file", tabindex: "-1", "aria-hidden": "true" });
        s.addMenu = h("div", { class: "ayc-menu", role: "menu", hidden: true, "aria-label": "Add content" },
            h("button", { type: "button", role: "menuitem", "data-act": "pick-files" }, icon("upload"), h("span", { text: "Choose files" })),
            h("button", { type: "button", role: "menuitem", "data-act": "pick-folder" }, icon("folder"), h("span", { text: "Choose a folder" })),
            h("p", { text: "Or drop files and folders anywhere on this screen." }));

        s.controls = h("div", { class: "ayc-controls" },
            h("label", { class: "ayu-agent-search ayc-search" }, icon("search"), s.search, s.searchClear),
            h("div", { class: "ayc-filters" }, s.agentSelect, s.collectionSelect, s.sortSelect, s.viewToggle));
        s.root = h("div", { class: "ayc-root", "data-ayc-root": "" },
            h("header", { class: "ayu-hero ayc-hero" }, h("div", { class: "ayu-hero-copy" }, s.title, s.sub), s.actions),
            s.banner, s.controls, s.kinds, s.agentRail, s.main, s.bulk, s.uploadPanel, s.live, s.fileInput, s.folderInput);
        s.layer = h("div", { class: "ayc-layer", "data-ayc-layer": "" });
        s.confirm = h("dialog", { class: "ayc-dialog", "aria-labelledby": "ayc-confirm-title" });
        s.storage = h("dialog", { class: "ayc-dialog ayc-dialog-wide", "aria-labelledby": "ayc-storage-title" });
        s.layer.appendChild(s.confirm);
        s.layer.appendChild(s.storage);
        s.sheetBackdrop = h("div", { class: "ayc-backdrop", hidden: true, "data-act": "close-preview" });
        s.layer.appendChild(s.sheetBackdrop);
        s.parking = document.createDocumentFragment();

        s.root.addEventListener("click", onClick);
        s.layer.addEventListener("click", onClick);
        s.root.addEventListener("keydown", onKey);
        s.layer.addEventListener("keydown", onKey);
        s.search.addEventListener("input", onSearch);
        [s.agentSelect, s.collectionSelect, s.sortSelect].forEach(function (el) { el.addEventListener("change", onSelect); });
        s.fileInput.addEventListener("change", function (e) { addFiles(Array.from(e.target.files)); e.target.value = ""; });
        s.folderInput.addEventListener("change", function (e) { addFiles(Array.from(e.target.files)); e.target.value = ""; });
        s.confirm.addEventListener("close", function () { restoreFocus(); });
        s.storage.addEventListener("close", function () { restoreFocus(); });
        if (window.IntersectionObserver) {
            s.observer = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) { if (entry.isIntersecting) { s.observer.unobserve(entry.target); wantThumb(entry.target); } });
            }, { rootMargin: "200px" });
        }
        s.wide.addEventListener && s.wide.addEventListener("change", function () { s.sigs.preview = ""; schedule(); });
    }

    // ---- mount / detach ------------------------------------------------------------------------------
    function mount(host, ctx) {
        if (!S) { create(); }
        S.ctx = ctx;
        S.host = host;
        S.mounted = true;
        clear(host);
        host.appendChild(S.root);
        if (!S.layer.parentNode) { document.body.appendChild(S.layer); }
        document.addEventListener("visibilitychange", onVisible);
        document.addEventListener("keydown", onDocumentKey);
        document.addEventListener("dragover", onDrag);
        document.addEventListener("dragleave", onDragLeave);
        document.addEventListener("drop", onDrop);
        document.addEventListener("click", onDocumentClick, true);
        if (S.restoreFocus) { var again = S.restoreFocus; S.restoreFocus = null; var el = findFocus(again); if (el) { el.focus({ preventScroll: true }); } }
        renderAll();
        if (!S.started) { S.started = true; start(); }
        else if (!S.polling) { pollSoon(300); }
    }
    function detach() {
        if (!S || !S.root.parentNode || S.root.parentNode === S.parking) { return; }
        var focused = document.activeElement;
        S.restoreFocus = focused && S.root.contains(focused) ? focusKey(focused) : null;
        S.parking.appendChild(S.root);
    }
    function unmount() {
        if (!S) { return; }
        S.mounted = false;
        S.polling = false;
        window.clearTimeout(S.poll.timer);
        document.removeEventListener("visibilitychange", onVisible);
        document.removeEventListener("keydown", onDocumentKey);
        document.removeEventListener("dragover", onDrag);
        document.removeEventListener("dragleave", onDragLeave);
        document.removeEventListener("drop", onDrop);
        document.removeEventListener("click", onDocumentClick, true);
        if (S.confirm.open) { S.confirm.close(); }
        if (S.storage.open) { S.storage.close(); }
        closePreview(true);
        detach();
    }
    function focusKey(el) {
        return { act: el.getAttribute("data-act") || "", id: el.getAttribute("data-id") || "", ayc: el.getAttribute("data-ayc") || "" };
    }
    // A redraw replaces elements; whatever had focus inside the redrawn area gets it back.
    function heldFocus(container) {
        var el = document.activeElement;
        return el && container.contains(el) ? { act: el.getAttribute("data-act") || "", id: el.getAttribute("id") || "", dataId: el.getAttribute("data-id") || "" } : null;
    }
    function restoreHeld(container, held) {
        if (!held) { return; }
        var selector = held.id ? "#" + cssEscape(held.id) : held.act ? '[data-act="' + held.act + '"]' + (held.dataId ? '[data-id="' + cssEscape(held.dataId) + '"]' : "") : "";
        var el = selector ? container.querySelector(selector) : null;
        if (el) { el.focus({ preventScroll: true }); }
    }
    function findFocus(key) {
        var selector = key.ayc ? '[data-ayc="' + key.ayc + '"]' : key.act ? '[data-act="' + key.act + '"]' + (key.id ? '[data-id="' + cssEscape(key.id) + '"]' : "") : "";
        return selector ? S.root.querySelector(selector) : null;
    }
    function cssEscape(value) { return window.CSS && CSS.escape ? CSS.escape(value) : String(value).replace(/"/g, '\\"'); }
    function restoreFocus() {
        var el = S.dialogOrigin;
        S.dialogOrigin = null;
        if (el && document.body.contains(el)) { el.focus({ preventScroll: true }); }
    }

    // ---- loading: summary, overview, then the walk ---------------------------------------------------------
    function start() {
        S.load = S.store.size() ? "ready" : "loading";
        S.error = "";
        schedule();
        loadSummary(true).then(function () {
            S.load = "ready";
            schedule();
            return Promise.all([loadGroups(), walk()]);
        }).then(function () { pollSoon(S.poll.wait); }).catch(function (error) {
            S.load = S.store.size() ? "ready" : "error";
            S.error = error.message;
            S.offline = unreachable(error);
            schedule();
            pollSoon(15000);
        });
    }
    var OLD_SERVER = "This computer's AutoYou-Server is older than this screen expects. Update the server to use My Content.";
    function clearAccessCache() {
        ++S.walkToken; ++S.serverToken;
        S.walking = false; S.groups = null; S.server = null;
        S.groupsKey = ""; S.serverKey = ""; S.selected.clear();
        S.thumbs.forEach(function (url) { URL.revokeObjectURL(url); });
        S.thumbs.clear(); S.thumbFail.clear(); S.thumbQueue = [];
        closePreview(true); S.sigs = {};
    }
    function loadSummary(force) {
        var headers = !force && S.etag ? { "If-None-Match": S.etag } : {};
        return api("/summary", { headers: headers }).catch(function (error) {
            if (error.status === 401 || error.status === 403) {
                S.store.reset(); S.store.summary = null; S.etag = "";
                clearAccessCache(); schedule();
            }
            if (error.status === 404 || error.status === 405) { var old = new Error(OLD_SERVER); old.status = 400; throw old; }
            throw error;
        }).then(function (result) {
            S.offline = false;
            if (result.notModified) { return false; }
            if (!result.body || !Array.isArray(result.body.agents) || typeof result.body.revision !== "number") { var skew = new Error(OLD_SERVER); skew.status = 400; throw skew; }
            S.etag = result.etag;
            if (S.store.setSummary(result.body)) { clearAccessCache(); }
            schedule();
            return true;
        });
    }
    function loadGroups() {
        if (S.store.walkComplete) { return Promise.resolve(); }
        var scope = S.store.summary && S.store.summary.access_scope;
        return api("/groups?per=6").then(function (result) {
            if (scope !== (S.store.summary && S.store.summary.access_scope)) { return; }
            if (!result.notModified && result.body && Array.isArray(result.body.groups)) { S.groups = result.body; S.groupsKey = String(result.body.revision); }
            schedule();
        }).catch(function () { /* the walk will fill the screen in */ });
    }
    function walk() {
        if (S.walking) { return Promise.resolve(); }
        S.walking = true;
        var ticket = ++S.walkToken;
        S.walkError = "";
        function page() {
            return api("/changes?after=" + S.store.cursor + "&limit=500").then(function (result) {
                if (ticket !== S.walkToken) { return; }
                if (result.notModified) { return; }
                if (!result.body || !Array.isArray(result.body.items)) { var skew = new Error(OLD_SERVER); skew.status = 400; throw skew; }
                S.store.applyChanges(result.body);
                schedule();
                if (result.body.has_more) { return page(); }
                S.store.walkComplete = true;
            });
        }
        return page().then(function () { if (ticket !== S.walkToken) { return; } S.walking = false; S.offline = false; schedule(); }, function (error) {
            if (ticket !== S.walkToken) { return; }
            S.walking = false;
            S.walkError = error.message;
            S.offline = unreachable(error);
            schedule();
            throw error;
        });
    }
    function catchUp() {
        // A walk that never finished starts over from its cursor; a finished one only fetches changes.
        return walk().then(function () {
            S.store.walkComplete = true;
            S.groups = null;
            pruneSelection();
            if (S.active && !S.store.get(S.active)) { closePreview(true); say("That item was removed."); }
            else if (S.active && S.preview && S.store.get(S.active).revision !== S.preview.item.revision && !S.editing) { openPreview(S.active, true); }
            schedule();
        });
    }
    function pruneSelection() {
        S.selected.forEach(function (id) { if (!S.store.get(id)) { S.selected.delete(id); } });
    }

    // ---- polling: one conditional request, then only what changed -----------------------------------------
    function pollSoon(wait) {
        window.clearTimeout(S.poll.timer);
        if (!S.mounted) { S.polling = false; return; }
        S.polling = true;
        S.poll.timer = window.setTimeout(tick, wait);
    }
    function tick() {
        if (!S.mounted) { return; }
        if (document.hidden || uploadingNow()) { pollSoon(S.poll.wait); return; }
        if (S.load === "error" || !S.store.summary) { start(); return; }
        loadSummary(false).then(function (changed) {
            var behind = S.store.summary && S.store.summary.revision > S.store.revision;
            S.poll.wait = changed || behind ? S.poll.floor : Math.min(S.poll.ceiling, Math.round(S.poll.wait * 1.6));
            if (behind) { return catchUp(); }
        }).catch(function (error) {
            S.offline = unreachable(error);
            S.error = S.store.size() ? "" : error.message;
            S.poll.wait = Math.min(S.poll.ceiling, S.poll.wait * 2);
            schedule();
        }).then(function () { pollSoon(S.poll.wait); });
    }
    function onVisible() { if (!document.hidden && S && S.mounted) { S.poll.wait = S.poll.floor; pollSoon(150); } }
    function uploadingNow() { return S.uploading; }

    // ---- the answer for the current filter ------------------------------------------------------------------
    function memo(name, key, compute) {
        var held = S.memos[name];
        if (held && held.key === key) { return held.value; }
        var value = compute();
        S.memos[name] = { key: key, value: value };
        return value;
    }
    function dataKey() { return [S.store.cursor, S.store.size(), S.store.walkComplete, JSON.stringify(S.filter), S.server ? S.server.revision : 0, S.serverKey].join("~"); }
    function results() {
        return memo("results", dataKey(), computeResults);
    }
    function computeResults() {
        var s = S;
        if (!s.store.walkComplete) { return s.server && s.serverKey === serverKey() ? s.server.items : []; }
        var local = s.store.query(s.filter);
        if (s.filter.text.trim().length >= 3 && s.server && s.serverKey === serverKey()) { local = s.store.withServerMatches(local, s.server.items); }
        return local;
    }
    function serverKey() { var f = S.filter; return [f.text.trim(), f.kind, f.source, f.collection, f.sort].join("\u0001"); }
    function askServer() {
        var key = serverKey(), token = ++S.serverToken, f = S.filter;
        if (S.store.walkComplete && f.text.trim().length < 3) { return; }
        if (!hasFilter() && S.store.walkComplete) { return; }
        var query = "?q=" + encodeURIComponent(f.text.trim()) + "&kind=" + encodeURIComponent(f.kind) + "&source=" + encodeURIComponent(f.source) +
            "&collection=" + encodeURIComponent(f.collection) + "&sort=" + f.sort + "&limit=" + (S.store.walkComplete ? 100 : 200);
        api("/index" + query).then(function (result) {
            if (token !== S.serverToken || result.notModified) { return; }
            S.server = result.body;
            S.serverKey = key;
            schedule();
        }).catch(function () { /* local answers still stand */ });
    }

    // ---- rendering --------------------------------------------------------------------------------------------
    function renderAll() {
        renderHero();
        renderBanner();
        renderControls();
        renderResults();
        syncMarks();
        renderPreview();
        renderBulk();
        renderUploads();
    }

    function renderHero() {
        var summary = S.store.summary;
        var subtitle = "Everything your agents and your phones have made, in one place.";
        if (summary && summary.total) {
            subtitle = plural(summary.total, "item") + " from " + plural(summary.agents.length, "agent") + (summary.bytes ? " · " + Store.formatBytes(summary.bytes) + " stored" : "");
            if (!S.store.walkComplete && S.store.size() < summary.total) { subtitle += " · indexing " + S.store.size().toLocaleString() + " of " + summary.total.toLocaleString(); }
        }
        var sig = [subtitle, can("upload"), can("manage"), S.syncing, S.store.summary && S.store.summary.worker && S.store.summary.worker.state].join("|");
        once("hero", sig, function () {
            S.sub.textContent = subtitle;
            clear(S.actions);
            S.actions.appendChild(button(S.syncing ? "Syncing…" : "Sync from agents", { icon: "refresh", act: "sync", variant: "ghost", extra: "ayc-compact", label: S.syncing ? "Syncing agent content" : "Sync from agents", disabled: S.syncing || !can("edit") }));
            if (can("manage")) { S.actions.appendChild(button("Storage", { icon: "sliders", act: "storage", variant: "secondary", extra: "ayc-compact", label: "Storage and organizing settings" })); }
            if (can("upload")) {
                var add = button("Add content", { icon: "plus", act: "toggle-add", variant: "primary" });
                add.setAttribute("aria-haspopup", "menu");
                add.setAttribute("aria-expanded", String(!S.addMenu.hidden));
                S.actions.appendChild(h("div", { class: "ayc-addwrap" }, add, S.addMenu));
            }
        });
    }

    function renderBanner() {
        var items = [];
        var summary = S.store.summary;
        if (S.offline && S.store.size()) {
            items.push(["amber", "alert", "Can't reach this computer right now. Showing what was loaded earlier; retrying automatically."]);
        } else if (S.walkError && S.load === "ready" && S.store.size()) {
            items.push(["red", "alert", S.walkError]);
        }
        if (summary && summary.worker && summary.worker.state === "indexing") { items.push(["blue", "refresh", "Finding new content from your agents…"]); }
        if (summary && summary.worker && summary.worker.scan_errors && summary.worker.scan_errors.length) { items.push(["amber", "alert", summary.worker.scan_errors.join(" · ")]); }
        if (summary && summary.attention && summary.attention.needs_attention) {
            items.push(["amber", "info", plural(summary.attention.needs_attention, "item") + " could not be organized. They stay listed; open one for details."]);
        }
        if (summary && summary.abilities && summary.abilities.role !== "owner" && !summary.abilities.delete) {
            items.push(["gray", "lock", "Remote access is set to " + summary.abilities.role + ". " + (summary.abilities.edit ? "You can organize and add content, but not delete it." : "You can browse but not change content.")]);
        }
        once("banner", JSON.stringify(items) + S.error, function () {
            clear(S.banner);
            items.forEach(function (entry) {
                var row = h("div", { class: "ayu-note ayu-note-" + entry[0] + " ayc-note", role: entry[0] === "red" ? "alert" : "status" }, icon(entry[1]), h("span", { text: entry[2] }));
                if (entry[0] === "red" || S.offline && entry[0] === "amber" && entry[2].indexOf("reach") !== -1) { row.appendChild(button("Try again", { act: "retry", small: true, variant: "secondary" })); }
                S.banner.appendChild(row);
            });
        });
    }

    function optionList(select, first, options, current) {
        var sig = JSON.stringify([first, options, current]);
        if (select.getAttribute("data-sig") === sig) { return; }
        select.setAttribute("data-sig", sig);
        clear(select);
        select.appendChild(h("option", { value: "", text: first }));
        options.forEach(function (pair) { select.appendChild(h("option", { value: pair[0], text: pair[1] })); });
        if (current && !options.some(function (pair) { return pair[0] === current; })) { select.appendChild(h("option", { value: current, text: current })); }
        select.value = current;
    }

    function renderControls() {
        var s = S, f = s.filter, summary = s.store.summary;
        if (document.activeElement !== s.search && s.search.value !== f.text) { s.search.value = f.text; }
        s.searchClear.hidden = !f.text;
        var ready = s.store.walkComplete;
        var facets = ready ? memo("facets", dataKey(), function () { return s.store.facets(f); }) : null;
        var agents = summary ? summary.agents : [];
        var collections = summary ? summary.collections : [];
        optionList(s.agentSelect, "All agents", agents.map(function (a) { return [a.source, a.name + " (" + (facets ? facets.sources[a.source] || 0 : a.count) + ")"]; }), f.source);
        optionList(s.collectionSelect, "All collections", collections.map(function (c) { return [c.name, c.name + " (" + (facets ? facets.collections[c.name] || 0 : c.count) + ")"]; }), f.collection);
        s.sortSelect.value = f.sort;
        Array.prototype.forEach.call(s.viewToggle.querySelectorAll("button"), function (b) {
            b.setAttribute("aria-pressed", String(b.getAttribute("data-id") === (overview() ? "agents" : "list")));
        });
        var kindCounts = facets ? facets.kinds : (summary ? summary.kinds : {});
        var present = Store.KINDS.filter(function (pair) { return kindCounts[pair[0]] || f.kind === pair[0]; });
        once("kinds", JSON.stringify([present, kindCounts, f.kind, !!summary, s.load]), function () {
            clear(s.kinds);
            if (!summary) { if (s.load !== "error") { chipSkeleton(s.kinds, 6); } return; }
            if (!present.length) { return; }
            s.kinds.appendChild(chip("All types", "", !f.kind, facets ? Object.keys(kindCounts).reduce(function (n, k) { return n + kindCounts[k]; }, 0) : summary.total, "kind"));
            present.forEach(function (pair) { s.kinds.appendChild(chip(pair[1], pair[0], f.kind === pair[0], kindCounts[pair[0]] || 0, "kind")); });
        });
        once("rail", JSON.stringify([agents.map(function (a) { return [a.source, a.name, a.count, a.accent, a.icon]; }), f.source, !!summary, s.load]), function () {
            clear(s.agentRail);
            if (!summary) { if (s.load !== "error") { chipSkeleton(s.agentRail, 5); } return; }
            if (agents.length < 2) { return; }
            s.agentRail.appendChild(chip("All agents", "", !f.source, summary.total, "source"));
            agents.forEach(function (a) { s.agentRail.appendChild(chip(a.name, a.source, f.source === a.source, a.count, "source", a)); });
        });
    }
    function chipSkeleton(parent, count) {
        for (var i = 0; i < count; i += 1) { parent.appendChild(h("span", { class: "ayc-skel-chip", "aria-hidden": "true" })); }
    }
    function chip(label, value, on, count, group, agent) {
        var el = h("button", { type: "button", class: "ayc-chip" + (on ? " is-on" : ""), "aria-pressed": String(on), "data-act": "chip", "data-group": group, "data-id": value },
            agent ? avatar(agent, "ayc-avatar-sm") : null, h("span", { class: "ayc-chip-label", text: label }), h("span", { class: "ayc-chip-count", text: String(count) }));
        return el;
    }
    function avatar(agent, extra) {
        var el = h("span", { class: "ayc-avatar" + (extra ? " " + extra : ""), "aria-hidden": "true" }, icon(ICONS[agent.icon] ? agent.icon : "agent"));
        if (agent.accent) { el.style.setProperty("--ayc-accent", agent.accent); el.classList.add("has-accent"); }
        return el;
    }

    // ---- results: overview shelves or one list ----------------------------------------------------------------
    function renderResults() {
        var s = S;
        var list = results();
        var shown = list.slice(0, s.limit);
        var mode = overview() ? "agents" : "list";
        var groups = null;
        if (mode === "agents") {
            groups = s.store.walkComplete ? s.store.groups(s.store.query(s.filter), 6) : (s.groups ? s.groups.groups : null);
        }
        var signature = [mode, s.load, list.length ? "" : s.store.walkComplete, serverKey(), s.store.signature(mode === "agents" && groups ? [].concat.apply([], groups.map(function (g) { return g.items; })) : shown),
            groups ? groups.map(function (g) { return g.source + g.count; }).join(",") : "", s.limit, list.length, !!s.store.summary, can("delete"), can("edit")].join("~");
        once("results", signature, function () {
            var held = heldFocus(s.results);
            drawResults();
            restoreHeld(s.results, held);
        });
        function drawResults() {
            clear(s.results);
            s.resultsPanel.setAttribute("aria-busy", String(s.load === "loading"));
            var total = s.store.summary ? s.store.summary.total : 0;
            if (s.load === "loading" && !s.store.size() && !(groups && groups.length)) { s.results.appendChild(skeleton()); head("Loading…", ""); return; }
            if (s.load === "error" && !s.store.size()) { s.results.appendChild(errorState()); head("", ""); return; }
            if (mode === "agents") {
                if (!groups || !groups.length) { s.results.appendChild(s.store.size() || total ? skeleton() : emptyState(false)); head(total ? "Latest from your agents" : "", ""); return; }
                head("Latest from your agents", plural(groups.length, "agent") + " · newest items first");
                groups.forEach(function (g) { s.results.appendChild(shelf(g)); });
                return;
            }
            var label = list.length === 1 ? "1 result" : list.length.toLocaleString() + " results";
            head(hasFilter() ? "Results" : "All content", label + (hasFilter() ? " for your filters" : ""));
            if (!list.length) {
                // Until the index is complete only the server's own answer to this exact filter can say "nothing".
                var waiting = !s.store.walkComplete && !(s.server && s.serverKey === serverKey());
                s.results.appendChild(waiting && s.load === "ready" ? skeleton() : emptyState(hasFilter()));
                return;
            }
            var listEl = h("div", { class: "ayc-list", role: "list", "aria-label": "Results" });
            shown.forEach(function (row) { listEl.appendChild(listRow(row)); });
            s.results.appendChild(listEl);
            if (list.length > shown.length) {
                s.results.appendChild(h("div", { class: "ayc-more" }, button("Show " + Math.min(PAGE, list.length - shown.length) + " more of " + (list.length - shown.length).toLocaleString() + " remaining", { act: "more", variant: "secondary" })));
            }
            say(label);
        }
        function head(title, meta) {
            clear(s.panelHead);
            s.panelHead.appendChild(h("h2", { text: title }));
            if (meta) { s.panelHead.appendChild(h("p", { text: meta })); }
        }
        observeThumbs();
    }

    // The open item and the selection change often; mark them in place so rows keep their focus.
    function syncMarks() {
        Array.prototype.forEach.call(S.results.querySelectorAll(".ayc-card[data-id]"), function (el) {
            el.classList.toggle("is-active", el.getAttribute("data-id") === S.active);
        });
        Array.prototype.forEach.call(S.results.querySelectorAll(".ayc-row[data-row]"), function (row) {
            var id = row.getAttribute("data-row"), picked = S.selected.has(id), box = row.querySelector('input[data-act="pick"]');
            row.classList.toggle("is-picked", picked);
            row.classList.toggle("is-active", id === S.active);
            if (box && box.checked !== picked) { box.checked = picked; }
        });
    }
    function skeleton() {
        var wrap = h("div", { class: "ayc-skeleton", "aria-hidden": "true" });
        for (var shelfIndex = 0; shelfIndex < 3; shelfIndex += 1) {
            var cards = h("div", { class: "ayc-cards" });
            for (var i = 0; i < 4; i += 1) { cards.appendChild(h("div", { class: "ayc-card ayc-skel" }, h("div", { class: "ayc-thumb" }), h("span", { class: "ayc-line" }), h("span", { class: "ayc-line short" }))); }
            wrap.appendChild(h("div", { class: "ayc-shelf" }, h("div", { class: "ayc-shelf-head" }, h("span", { class: "ayc-line med" })), cards));
        }
        return wrap;
    }
    function errorState() {
        return h("div", { class: "ayc-state", role: "alert" }, icon("alert", "ayc-state-icon"), h("h3", { text: "My Content could not be loaded" }),
            h("p", { text: S.error || "The computer did not answer. Check that the server is running and unlocked." }), button("Try again", { act: "retry", variant: "primary", icon: "refresh" }));
    }
    function emptyState(filtered) {
        if (filtered) {
            return h("div", { class: "ayc-state" }, icon("search", "ayc-state-icon"), h("h3", { text: "Nothing matches these filters" }),
                h("p", { text: "Try fewer words, or clear the filters to see everything again." }), button("Clear filters", { act: "clear-filters", variant: "secondary" }));
        }
        var actions = h("div", { class: "ayu-inline-actions ayc-state-actions" });
        if (can("upload")) { actions.appendChild(button("Add files", { act: "pick-files", variant: "primary", icon: "upload" })); }
        if (can("edit")) { actions.appendChild(button("Sync from agents", { act: "sync", variant: "secondary", icon: "refresh" })); }
        actions.appendChild(button("Manage agents", { act: "go-agents", variant: "ghost", icon: "agent" }));
        return h("div", { class: "ayc-state" }, icon("folder", "ayc-state-icon"), h("h3", { text: "Nothing here yet" }),
            h("p", { text: "Notes, pages, pictures, recordings and files from your agents appear here, along with anything saved by agents you made on your phone." }), actions);
    }

    function shelf(group) {
        var agent = agentOf(group.source);
        var head = h("div", { class: "ayc-shelf-head" }, avatar(agent), h("div", { class: "ayc-shelf-title" }, h("h3", { text: agent.name }),
            h("p", { text: plural(group.count, "item") + (agent.native ? " · from your phone" : "") })),
            h("div", { class: "ayc-shelf-actions" },
                group.count > group.items.length ? button("View all " + group.count, { act: "view-agent", id: group.source, small: true, variant: "ghost" }) : null,
                agent.app ? h("a", { class: "ayu-btn ayu-btn-secondary ayu-btn-sm", href: agent.app, target: "_blank", rel: "noopener noreferrer", "aria-label": "Open " + agent.name }, icon("external"), h("span", { text: "Open" })) : null));
        var cards = h("div", { class: "ayc-cards", role: "list", "aria-label": agent.name + " items" });
        group.items.forEach(function (row) { cards.appendChild(card(row)); });
        return h("section", { class: "ayc-shelf", "aria-label": agent.name }, head, cards);
    }

    function thumbFor(row, size) {
        var tile = h("div", { class: "ayc-thumb kind-" + row.kind }, icon(kindIcon(row.kind)));
        if (row.kind === "image" && row.has_file) {
            var img = h("img", { alt: "", decoding: "async", draggable: "false", "data-thumb": row.id, "data-rev": String(row.revision), "data-size": String(size) });
            tile.appendChild(img);
            var cached = S.thumbs.get(thumbKey(row.id, row.revision, size));
            if (cached) { img.src = cached; tile.classList.add("has-image"); }
        }
        return tile;
    }
    function thumbKey(id, revision, size) { return id + ":" + revision + ":" + size; }
    function card(row) {
        var agent = agentOf(row.source);
        return h("div", { class: "ayc-card-wrap", role: "listitem" },
            h("button", { type: "button", class: "ayc-card" + (S.active === row.id ? " is-active" : ""), "data-act": "open-preview", "data-id": row.id, "data-nav": "", "aria-label": row.title + ", " + singular(row.kind) + " from " + agent.name },
                thumbFor(row, 192), h("span", { class: "ayc-card-title", text: row.title }),
                h("span", { class: "ayc-card-meta", text: singular(row.kind) + " · " + Store.relativeTime(row.updated) })));
    }
    function listRow(row) {
        var agent = agentOf(row.source), picked = S.selected.has(row.id), collection = Store.collectionOf(row);
        var meta = [singular(row.kind), agent.name, collection, Store.relativeTime(row.updated)].filter(Boolean).join(" · ");
        var main = h("button", { type: "button", class: "ayc-rowmain", "data-act": "open-preview", "data-id": row.id, "data-nav": "", "aria-label": row.title + ", " + meta },
            thumbFor(row, 96), h("span", { class: "ayc-rowtext" }, h("span", { class: "ayc-rowtitle", text: row.title }), h("span", { class: "ayc-rowmeta", text: meta })));
        var chips = h("span", { class: "ayc-rowlabels" });
        Array.from(new Set((row.source_labels || []).concat(row.labels || []))).slice(0, 2).forEach(function (label) { chips.appendChild(h("span", { class: "ayc-label", text: label })); });
        var actions = h("span", { class: "ayc-rowactions" });
        var link = openLink(row);
        if (link) { actions.appendChild(link); }
        if (row.has_file) { actions.appendChild(h("a", { class: "ayc-iconbtn", href: S.ctx.url("/items/" + encodeURIComponent(row.id) + "/content?download=1"), download: "", "aria-label": "Download " + row.title, title: "Download" }, icon("download"))); }
        if (can("delete")) { actions.appendChild(iconButton("trash", "Delete " + row.title, "delete-one", row.id, "is-danger")); }
        return h("div", { class: "ayc-row" + (picked ? " is-picked" : "") + (S.active === row.id ? " is-active" : ""), role: "listitem", "data-row": row.id },
            can("edit") ? h("label", { class: "ayc-check" }, h("input", { type: "checkbox", checked: picked, "data-act": "pick", "data-id": row.id, "aria-label": "Select " + row.title }), h("span", { class: "ayc-check-box" }, icon("check"))) : h("span", { class: "ayc-check-gap" }),
            main, h("span", { class: "ayc-rowside" }, chips, row.byte_size ? h("span", { class: "ayc-size", text: Store.formatBytes(row.byte_size) }) : null), actions);
    }
    function itemUrl(row) {
        if (row.has_file) { return S.ctx.url("/items/" + encodeURIComponent(row.id) + "/content"); }
        var href = row.href || "";
        if (!href) { return ""; }
        if (/^\d+$/.test(row.source_id || "")) {
            if (row.source === "notes_agent") { return href + "#note-" + row.source_id; }
            if (row.source === "page_agent") { return href + "#item-" + row.source_id; }
        }
        return href;
    }
    function openLink(row) {
        var url = itemUrl(row);
        if (!url) { return null; }
        var agent = agentOf(row.source);
        var label = row.has_file ? "Open file" : "Open in " + agent.name;
        return h("a", { class: "ayc-iconbtn", href: url, target: "_blank", rel: "noopener noreferrer", "aria-label": label + ": " + row.title, title: label }, icon("external"));
    }

    // ---- thumbnails: lazy, four at a time, kept in memory only ------------------------------------------------------------
    function observeThumbs() {
        Array.prototype.forEach.call(S.results.querySelectorAll("img[data-thumb]"), function (img) {
            if (img.getAttribute("src")) { return; }
            if (S.observer) { S.observer.observe(img); } else { wantThumb(img); }
        });
    }
    function wantThumb(img) {
        var key = thumbKey(img.getAttribute("data-thumb"), img.getAttribute("data-rev"), img.getAttribute("data-size"));
        if (S.thumbFail.has(key)) { return; }
        S.thumbQueue.push({ img: img, key: key });
        pumpThumbs();
    }
    function pumpThumbs() {
        while (S.thumbBusy < 4 && S.thumbQueue.length) {
            var job = S.thumbQueue.shift();
            if (!job.img.isConnected && !S.root.contains(job.img)) { continue; }
            S.thumbBusy += 1;
            fetchThumb(job);
        }
    }
    function fetchThumb(job) {
        var scope = S.store.summary && S.store.summary.access_scope;
        var id = job.img.getAttribute("data-thumb"), size = job.img.getAttribute("data-size");
        var done = function () { S.thumbBusy -= 1; pumpThumbs(); };
        var cached = S.thumbs.get(job.key);
        if (cached) { job.img.src = cached; job.img.parentNode.classList.add("has-image"); done(); return; }
        S.ctx.request("/items/" + encodeURIComponent(id) + "/thumbnail?size=" + size, {}).then(function (response) {
            if (!response.ok) { throw new Error("none"); }
            return response.blob();
        }).then(function (blob) {
            if (scope !== (S.store.summary && S.store.summary.access_scope)) { return; }
            var url = URL.createObjectURL(blob);
            S.thumbs.set(job.key, url);
            if (S.thumbs.size > 240) { var oldest = S.thumbs.keys().next().value; URL.revokeObjectURL(S.thumbs.get(oldest)); S.thumbs.delete(oldest); }
            job.img.src = url;
            job.img.parentNode.classList.add("has-image");
        }).catch(function () { S.thumbFail.add(job.key); }).then(done);
    }

    // ---- preview ---------------------------------------------------------------------------------------------------------------
    function openPreview(id, quiet) {
        var row = S.store.get(id);
        if (!row) { return; }
        S.active = id;
        S.editing = quiet ? S.editing : false;
        var token = ++S.previewToken;
        if (!S.preview || S.preview.item.id !== id) { S.preview = null; S.previewState = "loading"; }
        S.sigs.preview = "";
        schedule();
        api("/items/" + encodeURIComponent(id) + "/preview").then(function (result) {
            if (token !== S.previewToken || result.notModified) { return; }
            S.preview = result.body;
            S.previewState = "ready";
            S.sigs.preview = "";
            schedule();
        }).catch(function (error) {
            if (token !== S.previewToken) { return; }
            S.previewState = "error";
            S.previewError = error.message;
            S.sigs.preview = "";
            schedule();
        });
        if (!quiet) { S.previewReturn = document.activeElement; }
    }
    function closePreview(silent) {
        if (!S.active && !silent) { return; }
        S.active = "";
        S.preview = null;
        S.previewState = "idle";
        S.editing = false;
        ++S.previewToken;
        S.sigs.preview = "";
        if (!silent) {
            var back = S.previewReturn;
            S.previewReturn = null;
            schedule();
            window.setTimeout(function () { if (back && document.body.contains(back)) { back.focus({ preventScroll: true }); } }, 0);
        } else { renderPreview(); }
    }
    function sheetMode() { return !S.wide.matches; }
    function renderPreview() {
        var s = S;
        var open = !!s.active;
        var sheet = open && sheetMode();
        if (s.preview_el.parentNode !== (sheet ? s.layer : s.main)) { (sheet ? s.layer : s.main).appendChild(s.preview_el); s.sigs.preview = ""; }
        s.preview_el.classList.toggle("is-sheet", sheet);
        s.preview_el.setAttribute("role", sheet ? "dialog" : "complementary");
        if (sheet) { s.preview_el.setAttribute("aria-modal", "true"); } else { s.preview_el.removeAttribute("aria-modal"); }
        s.sheetBackdrop.hidden = !sheet;
        document.documentElement.classList.toggle("ayc-lock", sheet);
        s.main.classList.toggle("has-preview", open && !sheet);
        s.preview_el.hidden = !open;
        if (!open) { s.previewShown = false; return; }
        var row = s.store.get(s.active);
        var signature = [s.active, s.previewState, s.preview ? s.preview.item.revision : 0, s.store.revision, s.editing, s.previewState === "error" ? s.previewError : "", can("delete"), can("edit"), sheet].join("~");
        once("preview", signature, function () {
            var held = heldFocus(s.previewBody);
            clear(s.previewBody);
            if (!row) { return; }
            var agent = agentOf(row.source);
            var data = s.preview;
            s.previewBody.appendChild(h("div", { class: "ayc-preview-head" },
                h("div", { class: "ayc-preview-title" }, h("span", { class: "ayc-preview-kind", text: singular(row.kind) + " · " + agent.name }), h("h2", { id: "ayc-preview-title", text: row.title })),
                iconButton("close", "Close details", "close-preview", "", "ayc-close")));
            s.preview_el.setAttribute("aria-labelledby", "ayc-preview-title");
            s.previewBody.appendChild(mediaFor(row, data));
            if (s.previewState === "loading") { s.previewBody.appendChild(h("div", { class: "ayc-skeleton", "aria-hidden": "true" }, h("span", { class: "ayc-line" }), h("span", { class: "ayc-line med" }), h("span", { class: "ayc-line short" }))); }
            if (s.previewState === "error") { s.previewBody.appendChild(h("div", { class: "ayu-note ayu-note-red ayc-note", role: "alert" }, icon("alert"), h("span", { text: s.previewError || "Details are unavailable." }), button("Try again", { act: "reload-preview", small: true }))); }
            if (data) { s.previewBody.appendChild(details(row, data, agent)); }
            restoreHeld(s.previewBody, held);
        });
        if (open && !s.previewShown) { s.previewShown = true; window.setTimeout(function () { var close = s.preview_el.querySelector(".ayc-close"); if (close && (sheetMode() || s.previewFocus)) { close.focus({ preventScroll: true }); } }, 0); }
    }
    function mediaFor(row, data) {
        var box = h("div", { class: "ayc-media kind-" + row.kind });
        var url = row.has_file ? S.ctx.url("/items/" + encodeURIComponent(row.id) + "/content") : "";
        var type = data && data.media ? data.media.type : "";
        if (row.kind === "image" && row.has_file) {
            var img = h("img", { alt: "Preview of " + row.title, class: "ayc-media-image", decoding: "async" });
            var key = thumbKey(row.id, row.revision, 640);
            if (S.thumbs.get(key)) { img.src = S.thumbs.get(key); }
            else {
                S.ctx.request("/items/" + encodeURIComponent(row.id) + "/thumbnail?size=640", {}).then(function (response) { if (!response.ok) { throw new Error("none"); } return response.blob(); }).then(function (blob) {
                    var objectUrl = URL.createObjectURL(blob);
                    S.thumbs.set(key, objectUrl);
                    img.src = objectUrl;
                }).catch(function () { img.replaceWith(h("div", { class: "ayc-media-fallback" }, icon("image"), h("span", { text: "No preview for this image" }))); });
            }
            box.appendChild(img);
        } else if (row.kind === "video" && url) {
            box.appendChild(h("video", { class: "ayc-media-player", controls: true, preload: "metadata", src: url, "aria-label": "Video: " + row.title }));
        } else if ((row.kind === "audio" || row.kind === "voice") && url) {
            box.appendChild(h("div", { class: "ayc-media-audio" }, icon(kindIcon(row.kind)), h("audio", { controls: true, preload: "metadata", src: url, "aria-label": "Audio: " + row.title })));
        } else if (type === "application/pdf" && url) {
            box.appendChild(h("iframe", { class: "ayc-document", src: url + "?preview=true", sandbox: "", title: row.title }));
        } else if (data && data.text) {
            box.classList.add("is-text");
            box.appendChild(h("pre", { class: "ayc-text", tabindex: "0", "aria-label": "Text preview" }, data.text));
            if (data.truncated) { box.appendChild(h("p", { class: "ayc-text-more", text: "Showing the first part. Open it to read everything." })); }
        } else {
            box.appendChild(h("div", { class: "ayc-media-fallback" }, icon(kindIcon(row.kind)), h("span", { text: type && row.has_file ? type : singular(row.kind) })));
        }
        return box;
    }
    function details(row, data, agent) {
        var wrap = h("div", { class: "ayc-details" });
        var item = data.item;
        var facts = [["Agent", agent.name + (agent.native ? " (from your phone)" : "")], ["Type", singular(row.kind)], ["Collection", Store.collectionOf(row) || "None"],
            ["Updated", new Date(row.updated * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })]];
        if (row.byte_size) { facts.push(["Size", Store.formatBytes(row.byte_size)]); }
        if (stateText(row)) { facts.push(["Status", stateText(row)]); }
        var dl = h("dl", { class: "ayc-facts" });
        facts.forEach(function (pair) { dl.appendChild(h("div", null, h("dt", { text: pair[0] }), h("dd", { text: pair[1] }))); });
        wrap.appendChild(dl);
        if ((item.labels || []).length) { wrap.appendChild(h("div", { class: "ayc-rowlabels ayc-labels", "aria-label": "Labels" }, item.labels.map(function (label) { return h("span", { class: "ayc-label", text: label }); }))); }
        if ((item.source_labels || []).length) {
            wrap.appendChild(h("p", { class: "ayc-hint", text: "Source tags · " + item.source_labels.join(", ") }));
        }
        var related = S.store.all().filter(function (child) { return child.parent_id === row.id; });
        if (related.length) {
            var attachments = h("div", { class: "ayc-related" }, h("h3", { text: "Attachments" }));
            related.forEach(function (child) {
                attachments.appendChild(button(child.title, { icon: kindIcon(child.kind), act: "open-preview", id: child.id, variant: "ghost" }));
            });
            wrap.appendChild(attachments);
        }
        var parent = item.parent_id && S.store.get(item.parent_id);
        if (parent) { wrap.appendChild(button("Back to " + parent.title, { act: "open-preview", id: parent.id, variant: "ghost" })); }
        if (item.annotation) { wrap.appendChild(h("p", { class: "ayc-annotation", text: item.annotation })); }
        var actions = h("div", { class: "ayu-inline-actions ayc-preview-actions" });
        var url = itemUrl(row);
        if (url) { actions.appendChild(h("a", { class: "ayu-btn ayu-btn-primary", href: url, target: "_blank", rel: "noopener noreferrer" }, icon("external"), h("span", { text: row.has_file ? "Open file" : "Open in " + agent.name }))); }
        if (row.has_file) { actions.appendChild(h("a", { class: "ayu-btn ayu-btn-secondary", href: S.ctx.url("/items/" + encodeURIComponent(row.id) + "/content?download=1"), download: "" }, icon("download"), h("span", { text: "Download" }))); }
        if (can("edit")) { actions.appendChild(button(S.editing ? "Cancel editing" : "Organize", { act: "toggle-edit", icon: "tag", variant: "secondary" })); }
        var mode = data.removal ? data.removal.mode : "catalog";
        if (mode === "open" && can("delete")) { actions.appendChild(h("a", { class: "ayu-btn ayu-btn-danger", href: agent.app || "#", target: "_blank", rel: "noopener noreferrer" }, icon("trash"), h("span", { text: "Delete in " + agent.name }))); }
        else if (can("delete")) { actions.appendChild(button("Delete", { act: "delete-one", id: row.id, icon: "trash", variant: "danger" })); }
        wrap.appendChild(actions);
        if (mode === "open") { wrap.appendChild(h("p", { class: "ayc-hint", text: agent.name + " keeps this item, so it is removed there." })); }
        if (S.editing && can("edit")) { wrap.appendChild(editor(row, item)); }
        return wrap;
    }
    function editor(row, item) {
        var collections = h("datalist", { id: "ayc-collections" });
        (S.store.summary ? S.store.summary.collections : []).forEach(function (c) { collections.appendChild(h("option", { value: c.name })); });
        var collection = h("input", { class: "ayu-input", id: "ayc-edit-collection", maxlength: "100", list: "ayc-collections", value: item.collection || "", placeholder: Store.collectionOf(row) || "No collection" });
        var labels = h("input", { class: "ayu-input", id: "ayc-edit-labels", maxlength: "1500", value: (item.labels || []).join(", "), placeholder: "Separate labels with commas" });
        var note = h("textarea", { class: "ayu-textarea", id: "ayc-edit-note", maxlength: "8000", rows: "4" });
        note.value = item.annotation || "";
        var status = h("p", { class: "ayc-edit-status", role: "status" });
        var form = h("form", { class: "ayc-edit", novalidate: true },
            h("div", { class: "ayu-field" }, h("label", { for: "ayc-edit-collection", text: "Collection" }), collection, collections),
            h("div", { class: "ayu-field" }, h("label", { for: "ayc-edit-labels", text: "Labels" }), labels,
                (item.suggested_labels || []).length ? h("button", { type: "button", class: "ayc-linkbtn", "data-act": "use-suggested", text: "Use suggested labels: " + item.suggested_labels.slice(0, 4).join(", ") }) : null),
            h("div", { class: "ayu-field" }, h("label", { for: "ayc-edit-note", text: "Note" }), note), status,
            h("div", { class: "ayu-inline-actions" }, h("button", { type: "submit", class: "ayu-btn ayu-btn-primary" }, icon("check"), h("span", { text: "Save changes" })), button("Cancel", { act: "toggle-edit" })));
        form.addEventListener("submit", function (event) {
            event.preventDefault();
            status.textContent = "Saving…";
            var body = { expected_revision: row.revision, collection: collection.value.trim(), annotation: note.value, labels: labels.value.split(",").map(function (x) { return x.trim(); }).filter(Boolean) };
            api("/items/" + encodeURIComponent(row.id), json("PATCH", body)).then(function (result) {
                S.editing = false;
                notify("success", "Saved changes to “" + row.title + "”.");
                return catchUp().then(function () { return loadSummary(true); }).then(function () { openPreview(row.id, true); }).then(function () { return result; });
            }).catch(function (error) {
                status.textContent = error.status === 409 ? "This item changed on another device. Reload it to see the latest version, then try again." : error.message;
                if (error.status === 409) { status.appendChild(document.createTextNode(" ")); status.appendChild(button("Reload", { act: "reload-preview", small: true })); }
            });
        });
        form.addEventListener("click", function (event) {
            if (event.target.getAttribute && event.target.getAttribute("data-act") === "use-suggested") { labels.value = (item.suggested_labels || []).join(", "); labels.focus(); }
        });
        return form;
    }

    // ---- selection and bulk bar -----------------------------------------------------------------------------------------------
    function renderBulk() {
        var count = S.selected.size;
        once("bulk", count + "|" + can("delete") + "|" + can("edit"), function () {
            S.bulk.hidden = !count;
            clear(S.bulk);
            if (!count) { return; }
            S.bulk.appendChild(h("span", { class: "ayc-bulk-count", text: plural(count, "item") + " selected" }));
            var actions = h("div", { class: "ayu-inline-actions" });
            if (can("edit")) { actions.appendChild(button("Add to collection", { act: "bulk-collect", icon: "folder", small: true })); actions.appendChild(button("Add label", { act: "bulk-label", icon: "tag", small: true })); }
            if (can("delete")) { actions.appendChild(button("Delete", { act: "bulk-delete", icon: "trash", small: true, variant: "danger" })); }
            actions.appendChild(button("Clear", { act: "bulk-clear", small: true, variant: "ghost" }));
            S.bulk.appendChild(actions);
        });
    }

    // ---- dialogs ---------------------------------------------------------------------------------------------------------------------
    function openDialog(dialog, origin) {
        S.dialogOrigin = origin || document.activeElement;
        if (!dialog.open) { dialog.showModal(); }
    }
    function plainWord(count, word) { return count === 1 ? word : word + "s"; }
    function askDelete(ids, origin) {
        var rows = ids.map(function (id) { return S.store.get(id); }).filter(Boolean);
        if (!rows.length) { return; }
        var groups = { catalog: [], agent: [], open: [] };
        rows.forEach(function (row) {
            var agent = agentOf(row.source);
            var mode = S.preview && S.preview.item.id === row.id && S.preview.removal ? S.preview.removal.mode : (agent.removal || "catalog");
            if (/^attachment:/.test(row.source_id || "") && mode === "agent") { mode = "open"; }
            groups[mode].push(row);
        });
        var doable = groups.catalog.concat(groups.agent);
        var dlg = S.confirm;
        clear(dlg);
        var single = rows.length === 1 ? rows[0] : null;
        var title = single ? "Delete “" + single.title + "”?" : "Delete " + plural(doable.length, "item") + "?";
        var lines = [];
        if (groups.catalog.length) {
            var files = groups.catalog.filter(function (row) { return row.has_file; }).length;
            lines.push(groups.catalog.length + " " + plainWord(groups.catalog.length, "item") + " will be removed from My Content" + (files ? " and " + plural(files, "stored file") + " deleted from this computer" : "") + ".");
        }
        if (groups.agent.length) {
            var names = Array.from(new Set(groups.agent.map(function (row) { return agentOf(row.source).name; })));
            lines.push(names.join(", ") + " will delete " + (groups.agent.length === 1 ? "it" : plural(groups.agent.length, "item")) + ", so " + (groups.agent.length === 1 ? "it disappears" : "they disappear") + " there too.");
        }
        if (groups.open.length) {
            var owners = Array.from(new Set(groups.open.map(function (row) { return agentOf(row.source).name; })));
            lines.push(plural(groups.open.length, "item") + " can only be deleted in " + owners.join(", ") + " and will be skipped.");
        }
        lines.push((groups.catalog.some(function (row) { return row.has_file; }) ? "Files outside AutoYou's own folders are never touched. " : "") + "This can't be undone.");
        var confirm = h("button", { type: "button", class: "ayu-btn ayu-btn-red", "data-act": "confirm-delete", disabled: !doable.length }, icon("trash"), h("span", { text: doable.length ? "Delete " + (doable.length === 1 ? "item" : plural(doable.length, "item")) : "Nothing to delete here" }));
        var cancel = h("button", { type: "button", class: "ayu-btn ayu-btn-secondary", "data-act": "cancel-dialog", autofocus: true }, h("span", { text: "Cancel" }));
        dlg.appendChild(h("div", { class: "ayc-dialog-body" },
            h("div", { class: "ayc-dialog-icon" }, icon("trash")),
            h("h2", { id: "ayc-confirm-title", text: title }),
            lines.map(function (line) { return h("p", { text: line }); }),
            h("p", { class: "ayc-dialog-error", role: "alert" }),
            h("div", { class: "ayu-inline-actions ayc-dialog-actions" }, cancel, confirm)));
        dlg.ayc = { targets: doable.map(function (row) { return { id: row.id, revision: row.revision, title: row.title }; }) };
        openDialog(dlg, origin);
        cancel.focus();
    }
    function runDelete() {
        var dlg = S.confirm, targets = dlg.ayc.targets, errorBox = dlg.querySelector(".ayc-dialog-error"), confirm = dlg.querySelector('[data-act="confirm-delete"]');
        confirm.disabled = true;
        errorBox.textContent = "";
        var request = targets.length === 1
            ? api("/items/" + encodeURIComponent(targets[0].id) + "?expected_revision=" + targets[0].revision, { method: "DELETE" }).then(function (r) { return { results: [Object.assign({ ok: true, id: targets[0].id }, r.body)], removed: 1 }; }, function (error) { return { results: [{ ok: false, id: targets[0].id, error: error.message, stale: !!(error.body && error.body.stale), mode: error.body && error.body.mode }], removed: 0 }; })
            : api("/items/delete", json("POST", { items: targets.map(function (t) { return { id: t.id, revision: t.revision }; }) })).then(function (r) { return r.body; });
        request.then(function (outcome) {
            var failed = outcome.results.filter(function (r) { return !r.ok; });
            outcome.results.filter(function (r) { return r.ok; }).forEach(function (r) { (r.removed || [r.id]).forEach(function (id) { S.store.applyChanges({ items: [{ id: id, deleted: 1, revision: Number.MAX_SAFE_INTEGER }] }); S.selected.delete(id); }); });
            if (S.active && !S.store.get(S.active)) { closePreview(true); }
            S.sigs = {};
            schedule();
            if (failed.length) {
                var stale = failed.filter(function (r) { return r.stale; }).length;
                errorBox.textContent = (outcome.removed ? plural(outcome.removed, "item") + " deleted. " : "") + (stale ? plural(stale, "item") + " changed on another device and was left alone. " : "") + (failed.length - stale ? failed.find(function (r) { return !r.stale; }).error : "");
                targets.splice(0, targets.length);
                confirm.hidden = true;
                dlg.querySelector('[data-act="cancel-dialog"]').textContent = "Close";
                catchUp().then(function () { return loadSummary(true); }).catch(function () {});
                return;
            }
            dlg.close();
            notify("success", outcome.removed === 1 ? "Deleted “" + (targets[0] ? targets[0].title : "item") + "”." : "Deleted " + plural(outcome.removed, "item") + ".");
            catchUp().then(function () { return loadSummary(true); }).catch(function () {});
            var next = S.results.querySelector("[data-nav]");
            if (next) { S.dialogOrigin = next; }
        }).catch(function (error) { errorBox.textContent = error.message; confirm.disabled = false; });
    }
    function askBulk(kind) {
        var ids = Array.from(S.selected), dlg = S.confirm;
        clear(dlg);
        var field = h("input", { class: "ayu-input", id: "ayc-bulk-value", maxlength: kind === "collection" ? "100" : "64", list: kind === "collection" ? "ayc-bulk-collections" : null, autocomplete: "off" });
        var list = h("datalist", { id: "ayc-bulk-collections" });
        (S.store.summary ? S.store.summary.collections : []).forEach(function (c) { list.appendChild(h("option", { value: c.name })); });
        var error = h("p", { class: "ayc-dialog-error", role: "alert" });
        var go = h("button", { type: "submit", class: "ayu-btn ayu-btn-primary" }, icon("check"), h("span", { text: "Apply to " + plural(ids.length, "item") }));
        var form = h("form", { class: "ayc-dialog-body" }, h("div", { class: "ayc-dialog-icon is-neutral" }, icon(kind === "collection" ? "folder" : "tag")),
            h("h2", { id: "ayc-confirm-title", text: kind === "collection" ? "Add " + plural(ids.length, "item") + " to a collection" : "Add a label to " + plural(ids.length, "item") }),
            h("div", { class: "ayu-field" }, h("label", { for: "ayc-bulk-value", text: kind === "collection" ? "Collection name" : "Label" }), field, list),
            error, h("div", { class: "ayu-inline-actions ayc-dialog-actions" }, h("button", { type: "button", class: "ayu-btn ayu-btn-secondary", "data-act": "cancel-dialog" }, h("span", { text: "Cancel" })), go));
        form.addEventListener("submit", function (event) {
            event.preventDefault();
            var value = field.value.trim();
            if (!value) { error.textContent = kind === "collection" ? "Type a collection name." : "Type a label."; field.focus(); return; }
            go.disabled = true;
            var body = { items: ids.map(function (id) { return { id: id, revision: S.store.get(id).revision }; }) };
            if (kind === "collection") { body.collection = value; } else { body.add_labels = [value]; }
            api("/items/organize", json("POST", body)).then(function (result) {
                var failed = result.body.results.filter(function (r) { return !r.ok; });
                S.sigs = {};
                if (failed.length) { error.textContent = plural(failed.length, "item") + " changed on another device and was skipped. " + plural(result.body.updated, "item") + " updated."; go.disabled = false; }
                else { dlg.close(); notify("success", "Updated " + plural(result.body.updated, "item") + "."); }
                return catchUp().then(function () { return loadSummary(true); });
            }).catch(function (e) { error.textContent = e.message; go.disabled = false; });
        });
        dlg.appendChild(form);
        openDialog(dlg);
        field.focus();
    }
    function openStorage() {
        var summary = S.store.summary, dlg = S.storage, current = summary ? summary.settings : { output_root: "", encrypted_at_rest: false, auto_organize: true, semantic_labels: true };
        clear(dlg);
        var folder = h("input", { class: "ayu-input", id: "ayc-folder", placeholder: "Absolute folder on this computer", value: current.output_root, autocomplete: "off" });
        var status = h("p", { class: "ayc-dialog-error", role: "status" });
        var auto = h("input", { type: "checkbox", id: "ayc-auto", checked: current.auto_organize });
        var labels = h("input", { type: "checkbox", id: "ayc-labels", checked: current.semantic_labels });
        var reminders = h("input", { type: "checkbox", id: "ayc-reminders", checked: current.index_reminders });
        var profile = h("input", { type: "checkbox", id: "ayc-profile", checked: current.index_profile });
        var priv = h("input", { type: "checkbox", id: "ayc-private", checked: current.index_private });
        var syncContent = h("input", { type: "checkbox", id: "ayc-content-sync", checked: current.content_sync_enabled !== false });
        var syncNotes = h("input", { type: "checkbox", id: "ayc-notes-sync", checked: current.notes_sync_enabled !== false });
        var save = h("button", { type: "button", class: "ayu-btn ayu-btn-primary", "data-act": "save-storage" }, icon("check"), h("span", { text: "Use this folder" }));
        dlg.appendChild(h("div", { class: "ayc-dialog-body" },
            h("div", { class: "ayc-dialog-head" }, h("h2", { id: "ayc-storage-title", text: "Storage and organizing" }), iconButton("close", "Close", "cancel-dialog", "", "ayc-close")),
            h("p", { text: "Existing items stay linked when you change the folder for new content." }),
            h("div", { class: "ayu-field" }, h("label", { for: "ayc-folder", text: "Output folder" }), folder),
            h("p", { class: "ayc-hint", text: current.encrypted_at_rest ? "Maximus is active. New managed outputs use encrypted storage. Linked external originals remain in their source folders." : "Maximus is not active. Turn it on in Security to encrypt new managed content at rest." }),
            h("label", { class: "ayu-checkbox" }, auto, h("span", null, "Organize new content automatically", h("small", { text: "Adds suggested labels and collections. You can always change them." }))),
            h("label", { class: "ayu-checkbox" }, labels, h("span", null, "Use the local AI for suggested labels", h("small", { text: "Runs on this computer. Without it, labels come from titles and agent names." }))),
            h("label", { class: "ayu-checkbox" }, reminders, h("span", null, "Include Reminders", h("small", { text: "Shows reminder times and messages to people with access to My Content." }))),
            h("label", { class: "ayu-checkbox" }, profile, h("span", null, "Include Profile facts", h("small", { text: "Lets people with access to My Content read your Profile preview." }))),
            h("label", { class: "ayu-checkbox" }, priv, h("span", null, "Include Diary entries", h("small", { text: "Lists each entry's title and first words here. The Diary keeps its own sign-in on other devices, which this does not replace. Turn off to remove them from My Content." }))),
            h("label", { class: "ayc-check", for: "ayc-content-sync" }, syncContent, h("span", { text: "Allow paired devices to sync agent collections" })),
            h("label", { class: "ayc-check", for: "ayc-notes-sync" }, syncNotes, h("span", { text: "Allow Notes sync from paired devices" })),
            status, h("div", { class: "ayu-inline-actions ayc-dialog-actions" }, button("Done", { act: "cancel-dialog" }), save)));
        save.addEventListener("click", function () {
            save.disabled = true;
            status.textContent = "Saving…";
            api("/settings", json("PUT", { output_root: folder.value })).then(function (result) { status.textContent = result.body.message || "Saved."; return loadSummary(true); })
                .catch(function (error) { status.textContent = error.message; }).then(function () { save.disabled = false; });
        });
        [auto, labels, priv, profile, reminders, syncNotes, syncContent].forEach(function (box) {
            box.addEventListener("change", function () {
                var controls = [auto, labels, priv, profile, reminders, syncNotes, syncContent];
                controls.forEach(function (control) { control.disabled = true; });
                api("/organization", json("PUT", { auto_organize: auto.checked, semantic_labels: labels.checked, index_private: priv.checked, index_profile: profile.checked, index_reminders: reminders.checked, notes_sync_enabled: syncNotes.checked, content_sync_enabled: syncContent.checked })).then(function () { status.textContent = "Saved."; if (box === priv || box === profile || box === reminders) { runSync(); } return loadSummary(true); },
                    function (error) { box.checked = !box.checked; status.textContent = error.message; })
                    .finally(function () { controls.forEach(function (control) { control.disabled = false; }); });
            });
        });
        openDialog(dlg);
    }

    // ---- uploads (resumable, one at a time) ---------------------------------------------------------------------------------------------------
    function addFiles(files) {
        if (!files.length || !can("upload")) { return; }
        S.addMenu.hidden = true;
        files.forEach(function (file) { S.uploads.push({ file: file, name: file.name, size: file.size, sent: 0, state: "waiting", error: "" }); });
        S.sigs.uploads = "";
        schedule();
        if (!S.uploading) { runUploads(); }
    }
    function relativePath(file) { return String(file.webkitRelativePath || file.relativePath || file.name || ""); }
    function runUploads() {
        S.uploading = true;
        var next = S.uploads.find(function (u) { return u.state === "waiting"; });
        if (!next) {
            S.uploading = false;
            var failed = S.uploads.filter(function (u) { return u.state === "failed"; }).length, done = S.uploads.filter(function (u) { return u.state === "done"; }).length;
            if (done) { notify(failed ? "warning" : "success", "Added " + plural(done, "file") + (failed ? "; " + failed + " could not be added." : ".")); S.poll.wait = S.poll.floor; pollSoon(200); }
            S.sigs.uploads = "";
            schedule();
            return;
        }
        next.state = "sending";
        S.sigs.uploads = "";
        schedule();
        uploadOne(next).then(function () { next.state = "done"; }, function (error) { next.state = "failed"; next.error = error.message; }).then(function () { S.sigs.uploads = ""; schedule(); runUploads(); });
    }
    function uploadOne(item) {
        var file = item.file, key = "autoyou-content-upload:" + JSON.stringify([relativePath(file), file.size, file.lastModified]), upload = null, saved = "";
        try { saved = window.sessionStorage.getItem(key) || ""; } catch (e) { saved = ""; }
        var start = saved ? api("/upload-sessions/" + encodeURIComponent(saved)).then(function (r) { return r.body; }, function () { return null; }) : Promise.resolve(null);
        return start.then(function (found) {
            if (found) { return found; }
            return api("/upload-sessions", json("POST", { filename: file.name, size: file.size })).then(function (r) { return r.body; });
        }).then(function (session) {
            upload = session;
            try { window.sessionStorage.setItem(key, upload.id); } catch (e) { /* resuming is optional */ }
            function chunk() {
                if (upload.received >= file.size) { return Promise.resolve(); }
                var body = file.slice(upload.received, upload.received + upload.chunk_bytes);
                return api("/upload-sessions/" + encodeURIComponent(upload.id) + "?offset=" + upload.received, { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body: body }).then(function (r) {
                    upload = r.body;
                    item.sent = upload.received;
                    S.sigs.uploads = "";
                    schedule();
                    return chunk();
                });
            }
            return chunk();
        }).then(function () { return api("/upload-sessions/" + encodeURIComponent(upload.id) + "/complete", { method: "POST" }); }).then(function (done) {
            try { window.sessionStorage.removeItem(key); } catch (e) { /* ignore */ }
            var path = relativePath(file);
            if (path.indexOf("/") !== -1) {
                var row = done.body.item;
                return api("/items/" + encodeURIComponent(row.id), json("PATCH", { expected_revision: row.revision, labels: row.labels, annotation: "", collection: path.split("/").slice(0, -1).join("/").slice(0, 100) }));
            }
        });
    }
    function renderUploads() {
        var list = S.uploads;
        var sig = list.map(function (u) { return u.name + u.state + u.sent; }).join("|");
        once("uploads", sig, function () {
            clear(S.uploadPanel);
            S.uploadPanel.hidden = !list.length;
            if (!list.length) { return; }
            var active = list.filter(function (u) { return u.state === "waiting" || u.state === "sending"; });
            S.uploadPanel.appendChild(h("div", { class: "ayc-uploads-head" }, h("strong", { text: active.length ? "Adding " + plural(active.length, "file") + "…" : "Finished adding files" }),
                active.length ? null : button("Dismiss", { act: "dismiss-uploads", small: true, variant: "ghost" })));
            list.slice(-5).forEach(function (u) {
                var pct = u.size ? Math.min(100, Math.round(u.sent / u.size * 100)) : 100;
                var bar = h("div", { class: "ayu-progress", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(u.state === "done" ? 100 : pct), "aria-label": "Progress for " + u.name }, h("div", { class: "ayu-progress-fill" + (u.state === "failed" ? " failed" : ""), style: "width:" + (u.state === "done" ? 100 : pct) + "%" }));
                S.uploadPanel.appendChild(h("div", { class: "ayc-upload" }, h("span", { class: "ayc-upload-name", text: u.name }), h("span", { class: "ayc-upload-state", text: u.state === "failed" ? u.error : u.state === "done" ? "Added" : u.state === "sending" ? pct + "%" : "Waiting" }), bar));
            });
        });
    }
    function onDrag(event) {
        if (!S.mounted || !can("upload") || !event.dataTransfer || Array.from(event.dataTransfer.types || []).indexOf("Files") === -1) { return; }
        event.preventDefault();
        S.root.classList.add("is-dropping");
    }
    function onDragLeave(event) { if (!event.relatedTarget && S) { S.root.classList.remove("is-dropping"); } }
    function onDrop(event) {
        if (!S.mounted || !event.dataTransfer || Array.from(event.dataTransfer.types || []).indexOf("Files") === -1) { return; }
        event.preventDefault();
        S.root.classList.remove("is-dropping");
        if (!can("upload")) { notify("warning", "This account can't add content."); return; }
        var transfer = event.dataTransfer;
        var base = S.ctx.assetBase || "";
        // Folders need the shared folder reader; plain files can be taken as they are.
        import(base + "content-drop.js").then(function (module) { return module.collectDroppedFiles(transfer, function (count) { say("Found " + count + " files"); }); })
            .catch(function () { return Array.from(transfer.files || []); }).then(addFiles);
    }

    // ---- events --------------------------------------------------------------------------------------------------------------------------------
    function onSearch() {
        S.filter.text = S.search.value;
        S.limit = PAGE;
        window.clearTimeout(S.searchTimer);
        S.searchTimer = window.setTimeout(askServer, 300);
        schedule();
    }
    function onSelect(event) {
        var key = event.target.getAttribute("data-ayc");
        S.filter[key] = event.target.value;
        if (key === "sort") { S.filter.sort = event.target.value || "updated"; }
        S.limit = PAGE;
        S.selected.clear();
        askServer();
        schedule();
    }
    function onDocumentClick(event) {
        if (!S.addMenu.hidden && !S.addMenu.contains(event.target) && !(event.target.closest && event.target.closest('[data-act="toggle-add"]'))) { S.addMenu.hidden = true; var b = S.actions.querySelector('[data-act="toggle-add"]'); if (b) { b.setAttribute("aria-expanded", "false"); } }
    }
    function onClick(event) {
        var target = event.target.closest ? event.target.closest("[data-act]") : null;
        if (!target || !(S.root.contains(target) || S.layer.contains(target))) { return; }
        var act = target.getAttribute("data-act"), id = target.getAttribute("data-id") || "";
        switch (act) {
            case "open-preview": S.previewFocus = false; openPreview(id); break;
            case "close-preview": closePreview(); break;
            case "reload-preview": catchUp().then(function () { return loadSummary(true); }).then(function () { if (S.active) { S.editing = false; openPreview(S.active, true); } }); break;
            case "chip": {
                var group = target.getAttribute("data-group");
                S.filter[group] = S.filter[group] === id ? "" : id;
                S.limit = PAGE; S.selected.clear(); askServer(); schedule(); break;
            }
            case "view": S.view = id; if (id === "agents") { clearFilters(true); } else { askServer(); } schedule(); break;
            case "view-agent": S.filter.source = id; S.view = "list"; S.limit = PAGE; askServer(); schedule(); break;
            case "clear-search": S.filter.text = ""; S.search.value = ""; S.search.focus(); askServer(); schedule(); break;
            case "clear-filters": clearFilters(); break;
            case "more": S.limit += PAGE; schedule(); break;
            case "retry": S.error = ""; S.walkError = ""; S.offline = false; S.load = S.store.size() ? "ready" : "loading"; schedule(); start(); break;
            case "sync": runSync(); break;
            case "storage": openStorage(); break;
            case "go-agents": if (S.ctx.navigate) { S.ctx.navigate("agents"); } break;
            case "toggle-add": {
                S.addMenu.hidden = !S.addMenu.hidden;
                target.setAttribute("aria-expanded", String(!S.addMenu.hidden));
                if (!S.addMenu.hidden) { var first = S.addMenu.querySelector("button"); if (first) { first.focus(); } }
                break;
            }
            case "pick-files": S.addMenu.hidden = true; S.fileInput.click(); break;
            case "pick-folder": S.addMenu.hidden = true; S.folderInput.click(); break;
            case "dismiss-uploads": S.uploads = S.uploads.filter(function (u) { return u.state === "sending" || u.state === "waiting"; }); S.sigs.uploads = ""; schedule(); break;
            case "toggle-edit": S.editing = !S.editing; S.sigs.preview = ""; schedule(); break;
            case "pick": pickRow(id, event); return;
            case "bulk-clear": S.selected.clear(); S.sigs = {}; schedule(); break;
            case "bulk-delete": askDelete(Array.from(S.selected), target); break;
            case "bulk-collect": askBulk("collection"); break;
            case "bulk-label": askBulk("label"); break;
            case "delete-one": askDelete([id], target); break;
            case "confirm-delete": runDelete(); break;
            case "cancel-dialog": { var dialog = target.closest("dialog"); if (dialog) { dialog.close(); } break; }
            default: break;
        }
    }
    function pickRow(id, event) {
        var rows = results().slice(0, S.limit).map(function (r) { return r.id; });
        if (event.shiftKey && S.lastPicked && rows.indexOf(S.lastPicked) !== -1) {
            var a = rows.indexOf(S.lastPicked), b = rows.indexOf(id);
            rows.slice(Math.min(a, b), Math.max(a, b) + 1).forEach(function (rowId) { S.selected.add(rowId); });
        } else if (event.target.checked) { S.selected.add(id); } else { S.selected.delete(id); }
        S.lastPicked = id;
        S.sigs.bulk = "";
        schedule();
    }
    function clearFilters(keepView) {
        S.filter = { text: "", kind: "", source: "", collection: "", sort: S.filter.sort };
        S.search.value = "";
        S.server = null;
        S.limit = PAGE;
        if (!keepView) { S.view = "agents"; }
        schedule();
    }
    function runSync() {
        if (S.syncing) { return; }
        S.syncing = true;
        S.sigs.hero = "";
        schedule();
        api("/refresh-existing", { method: "POST" }).then(function (result) {
            say("Syncing agent content");
            if (!result.body.queued) { notify(result.body.errors && result.body.errors.length ? "warning" : "success", result.body.errors && result.body.errors.length ? result.body.errors.join(" · ") : "Agent content synced."); }
            S.poll.wait = S.poll.floor;
            return loadSummary(true).then(catchUp);
        }).catch(function (error) { notify("error", error.message); }).then(function () { S.syncing = false; S.sigs.hero = ""; schedule(); });
    }
    function onKey(event) {
        var target = event.target, key = event.key;
        var typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
        if (key === "Escape") {
            if (!S.addMenu.hidden) { S.addMenu.hidden = true; var trigger = S.actions.querySelector('[data-act="toggle-add"]'); if (trigger) { trigger.setAttribute("aria-expanded", "false"); trigger.focus(); } event.stopPropagation(); return; }
            if (target === S.search && S.search.value) { S.filter.text = ""; S.search.value = ""; askServer(); schedule(); event.preventDefault(); return; }
            if (S.active && !S.confirm.open && !S.storage.open) { closePreview(); event.preventDefault(); }
            return;
        }
        if (sheetMode() && S.active && key === "Tab" && S.preview_el.contains(target)) { trapFocus(event); return; }
        if (!target.hasAttribute || !target.hasAttribute("data-nav")) { return; }
        var moves = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1, Home: "first", End: "last" };
        if (moves[key] !== undefined) {
            var nav = Array.prototype.slice.call(S.results.querySelectorAll("[data-nav]")), index = nav.indexOf(target);
            var next = moves[key] === "first" ? 0 : moves[key] === "last" ? nav.length - 1 : index + moves[key];
            if (nav[next]) { nav[next].focus(); event.preventDefault(); }
        } else if ((key === "Delete" || key === "Backspace") && can("delete") && !typing) {
            var id = target.getAttribute("data-id");
            if (id) { event.preventDefault(); askDelete(S.selected.size && S.selected.has(id) ? Array.from(S.selected) : [id], target); }
        }
    }
    // "/" jumps to search from anywhere on the screen, including when nothing has focus yet.
    function onDocumentKey(event) {
        if (event.key !== "/" || event.ctrlKey || event.metaKey || event.altKey || !S.mounted || event.defaultPrevented) { return; }
        var target = event.target;
        var typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT" || target.isContentEditable);
        var dialogOpen = S.confirm.open || S.storage.open;
        if (typing || dialogOpen || (target !== document.body && !S.root.contains(target))) { return; }
        event.preventDefault();
        S.search.focus();
    }
    function trapFocus(event) {
        var focusable = Array.prototype.filter.call(S.preview_el.querySelectorAll('a[href], button:not([disabled]), input, select, textarea, video[controls], audio[controls], [tabindex="0"]'), function (el) { return !el.hidden && el.offsetParent !== null; });
        if (!focusable.length) { return; }
        var first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) { last.focus(); event.preventDefault(); }
        else if (!event.shiftKey && document.activeElement === last) { first.focus(); event.preventDefault(); }
    }

    window.AutoYouContent = {
        mount: mount, detach: detach, unmount: unmount,
        // Read-only view of the screen's state for tests and diagnostics.
        _state: function () { return S; }
    };
}());
