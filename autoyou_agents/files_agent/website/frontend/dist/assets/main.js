// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-D-to-3dd6058bce78f70fa5ca0d09
const skinAccents = { professional: "#2563eb", midnight: "#709dff", neon: "#00b9cc" };
let appearance = { skin: "professional", accent: skinAccents.professional, density: "comfortable" };
const appearanceKey = "autoyou.files.appearance.v1";
let sortMode = "name";
let showHidden = false;
let truncated = false;
let directoryRequest = 0;
let previewRequest = 0;
function svgIcon(name) {
    const paths = {
        folder: "M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11H3Z",
        file: "M6 3h8l4 4v14H6Z M14 3v5h4 M9 12h6 M9 16h6",
        home: "m3 10 9-7 9 7 M5 9v12h14V9 M10 21v-8h4v8",
        drive: "m5 4-3 10v6h20v-6L19 4Z M2 14h20 M16 17h.01 M19 17h.01",
        image: "M3 3h18v18H3Z m0 13 5-5 5 5 3-3 5 5 M16 7h.01",
        back: "m14 5-7 7 7 7", up: "m5 13 7-7 7 7 M12 6v15",
        copy: "M8 8h13v13H8Z M16 8V3H3v13h5",
        refresh: "M20 7a9 9 0 1 0 1 8 M20 2v6h-6",
        search: "M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
        settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2",
        list: "M8 6h13 M8 12h13 M8 18h13 M3 6h.01 M3 12h.01 M3 18h.01",
        grid: "M3 3h7v7H3Z M14 3h7v7h-7Z M3 14h7v7H3Z M14 14h7v7h-7Z",
        close: "m6 6 12 12 M6 18 18 6",
    };
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.6");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", paths[name] || paths.file);
    svg.append(path);
    return svg;
}
function applyAppearance(save = true) {
    document.documentElement.dataset.skin = appearance.skin;
    document.documentElement.dataset.density = appearance.density;
    document.documentElement.style.setProperty("--blue", appearance.accent);
    const rgb = appearance.accent.match(/[0-9a-f]{2}/gi).map((part) => parseInt(part, 16) / 255).map((value) => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
    const luminance = rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
    document.documentElement.style.setProperty("--accent-ink", luminance > .179 ? "#101820" : "#ffffff");
    document.querySelectorAll('input[name="skin"]').forEach((input) => { input.checked = input.value === appearance.skin; });
    byId("accent-input").value = appearance.accent;
    byId("density-select").value = appearance.density;
    if (save) {
        try {
            localStorage.setItem(appearanceKey, JSON.stringify(appearance));
        }
        catch {
            showToast("Appearance applies here. Browser storage is unavailable.");
        }
    }
}
const state = {
    currentPath: "",
    parentPath: null,
    entries: [],
    locations: [],
    selected: null,
    preview: null,
    query: "",
    view: "list",
    history: [],
    loading: true,
};
function byId(id) {
    const element = document.getElementById(id);
    if (!element) {
        throw new Error(`Missing element #${id}`);
    }
    return element;
}
function apiUrl(path, params = {}) {
    const url = new URL(path, window.location.href);
    for (const [key, value] of Object.entries(params)) {
        if (value) {
            url.searchParams.set(key, value);
        }
    }
    return url.toString();
}
async function apiGet(path, params = {}) {
    const response = await fetch(apiUrl(path, params), { credentials: "same-origin" });
    const payload = (await response.json().catch(() => ({
        success: false,
        error: response.statusText,
    })));
    if (!response.ok || payload.success === false) {
        throw new Error(payload.error || response.statusText || "Request failed");
    }
    return payload;
}
function text(tagName, className, value) {
    const element = document.createElement(tagName);
    element.className = className;
    element.textContent = value;
    return element;
}
function formatBytes(bytes) {
    if (bytes === null || bytes === undefined) {
        return "-";
    }
    if (bytes < 1024) {
        return `${bytes} B`;
    }
    const units = ["KB", "MB", "GB", "TB"];
    let value = bytes / 1024;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}
function formatDate(value) {
    if (!value) {
        return "-";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return value;
    }
    return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
    }).format(date);
}
function kindLabel(entry) {
    if (entry.kind === "directory") {
        return "Folder";
    }
    if (entry.preview_kind === "image") {
        return "Image";
    }
    if (entry.preview_kind === "text") {
        return "Text";
    }
    if (entry.extension) {
        return `${entry.extension.slice(1).toUpperCase()} file`;
    }
    return entry.kind === "other" ? "Item" : "File";
}
function iconLabel(entry) {
    if (entry.kind === "directory") {
        return "DIR";
    }
    if (entry.preview_kind === "image") {
        return "IMG";
    }
    if (entry.preview_kind === "text") {
        return "TXT";
    }
    return "FILE";
}
function showToast(message) {
    const toast = byId("toast");
    toast.textContent = message;
    toast.classList.add("visible");
    window.setTimeout(() => toast.classList.remove("visible"), 1800);
}
function showError(message) {
    byId("status-line").textContent = message;
    showToast(message);
}
function matchesQuery(entry) {
    const query = state.query.trim().toLowerCase();
    if (!query) {
        return true;
    }
    return [entry.name, entry.path, entry.extension || "", entry.kind].join(" ").toLowerCase().includes(query);
}
function filteredEntries() {
    return state.entries.filter((entry) => (showHidden || !entry.hidden) && matchesQuery(entry)).sort((a, b) => {
        const folders = Number(b.kind === "directory") - Number(a.kind === "directory");
        if (folders)
            return folders;
        if (sortMode === "size")
            return (b.size_bytes || 0) - (a.size_bytes || 0) || a.name.localeCompare(b.name);
        if (sortMode === "modified")
            return String(b.modified || "").localeCompare(String(a.modified || "")) || a.name.localeCompare(b.name);
        return a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });
    });
}
function isInLocation(path, location) {
    const windows = /^[A-Za-z]:|^\\\\/.test(path);
    const current = windows ? path.toLowerCase() : path;
    const base = windows ? location.toLowerCase() : location;
    if (current === base) {
        return true;
    }
    if (base.endsWith("\\") || base.endsWith("/")) {
        return current.startsWith(base);
    }
    return current.startsWith(`${base}\\`) || current.startsWith(`${base}/`);
}
function renderLocations() {
    const root = byId("locations-list");
    byId("location-count").textContent = `${state.locations.length} locations`;
    root.replaceChildren();
    for (const [label, locations] of [["Quick access", state.locations.filter((row) => row.kind !== "drive")], ["Drives", state.locations.filter((row) => row.kind === "drive")]]) {
        if (!locations.length)
            continue;
        root.append(text("p", "location-group", label));
        const matching = state.locations.filter((row) => isInLocation(state.currentPath, row.path)).sort((a, b) => b.path.length - a.path.length)[0];
        root.append(...locations.map((location) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "location-button";
            const active = Boolean(state.currentPath) && matching === location;
            button.classList.toggle("active", active);
            if (active)
                button.setAttribute("aria-current", "location");
            button.title = location.path;
            const locationIcon = text("span", "location-icon", "");
            locationIcon.append(svgIcon(location.kind === "drive" ? "drive" : location.kind === "home" ? "home" : "folder"));
            button.append(locationIcon, text("span", "location-label", location.label), text("span", "location-path", location.path));
            if (location.total_bytes && location.free_bytes !== undefined) {
                const capacity = text("span", "capacity", "");
                const fill = document.createElement("span");
                fill.style.width = `${Math.max(0, Math.min(100, 100 * (location.total_bytes - location.free_bytes) / location.total_bytes))}%`;
                capacity.append(fill);
                button.append(capacity, text("span", "capacity-label", `${formatBytes(location.free_bytes)} free of ${formatBytes(location.total_bytes)}`));
            }
            button.addEventListener("click", () => {
                void loadDirectory(location.path);
            });
            return button;
        }));
    }
}
function renderBreadcrumbs() {
    const path = state.currentPath;
    const separator = path.includes("\\") ? "\\" : "/";
    const pieces = path.split(/[\\/]/).filter(Boolean);
    const root = byId("breadcrumbs");
    root.replaceChildren();
    byId("folder-title").textContent = pieces[pieces.length - 1] || "Computer";
    // UNC paths start at the share, never at a bare server name.
    const unc = path.startsWith("\\\\");
    const start = unc ? 2 : 0;
    if (unc) {
        pieces.splice(0, 2, pieces.slice(0, 2).join(separator));
    }
    const crumbs = separator === "/" && path.startsWith("/") ? ["/", ...pieces] : pieces;
    for (let index = 0; index < crumbs.length; index++) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = crumbs[index];
        let target = crumbs.slice(0, index + 1).join(separator).replace(/^\/\//, "/");
        if (unc)
            target = "\\\\" + crumbs.slice(0, index + 1).join(separator);
        if (separator === "\\" && index === 0 && !start)
            target += "\\";
        button.addEventListener("click", () => void loadDirectory(target));
        if (index)
            root.append(text("span", "breadcrumb-separator", "/"));
        if (index === crumbs.length - 1)
            button.setAttribute("aria-current", "page");
        root.append(button);
    }
}
function renderEntry(entry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = state.view === "grid" ? "entry-card" : "entry-row";
    button.classList.toggle("active", state.selected?.path === entry.path);
    const main = text("span", "entry-main", "");
    const fileIcon = text("span", `file-icon ${entry.kind} ${entry.preview_kind || ""}`, "");
    fileIcon.append(svgIcon(entry.kind === "directory" ? "folder" : entry.preview_kind === "image" ? "image" : "file"));
    main.append(fileIcon, text("span", "entry-name", entry.name));
    button.title = entry.name;
    button.setAttribute("aria-pressed", String(state.selected?.path === entry.path));
    button.append(main, text("span", "entry-kind", kindLabel(entry)), text("span", "entry-modified", formatDate(entry.modified)), text("span", "entry-size", entry.kind === "directory" ? "" : formatBytes(entry.size_bytes)));
    button.addEventListener("click", () => {
        if (entry.kind === "directory") {
            void loadDirectory(entry.path);
        }
        else {
            void selectEntry(entry);
        }
    });
    return button;
}
function renderEntries() {
    const root = byId("entries");
    root.className = `entries ${state.view}-view`;
    root.setAttribute("aria-busy", String(state.loading));
    byId("path-input").value = state.currentPath;
    byId("back-button").disabled = state.history.length === 0;
    byId("up-button").disabled = !state.parentPath;
    if (state.loading) {
        root.replaceChildren(text("div", "empty-state", "Loading folder..."));
        return;
    }
    const rows = filteredEntries();
    byId("status-line").textContent = `${rows.length} item${rows.length === 1 ? "" : "s"}${state.query ? " matched" : ""}${truncated ? " - folder listing limited to 400 items" : ""}`;
    if (!rows.length) {
        root.replaceChildren(text("div", "empty-state", state.query ? "No matching items." : "This folder is empty."));
        return;
    }
    root.replaceChildren(...rows.map(renderEntry));
}
function metaRows(entry) {
    const rows = [
        ["Path", entry.path],
        ["Type", kindLabel(entry)],
        ["Size", entry.kind === "directory" ? "-" : formatBytes(entry.size_bytes)],
        ["Modified", formatDate(entry.modified)],
    ];
    const grid = document.createElement("div");
    grid.className = "meta-grid";
    for (const [label, value] of rows) {
        const row = document.createElement("div");
        row.append(text("span", "", label), text("strong", "", value));
        grid.append(row);
    }
    return grid;
}
function renderPreview() {
    const title = byId("preview-title");
    const meta = byId("preview-meta");
    const body = byId("preview-body");
    const preview = state.preview;
    if (!preview) {
        title.textContent = "A closer look";
        meta.textContent = "FILE DETAILS";
        const empty = text("div", "empty-state", "");
        empty.append(svgIcon("file"), text("p", "", "Select a file to see its preview and details."));
        body.replaceChildren(empty);
        return;
    }
    title.textContent = preview.name;
    meta.textContent = `${kindLabel(preview)} - ${formatBytes(preview.size_bytes)}`;
    if (preview.kind === "directory") {
        body.replaceChildren(metaRows(preview));
    }
    else if (preview.preview_kind === "image" && preview.data_uri) {
        const image = document.createElement("img");
        image.className = "image-preview";
        image.src = preview.data_uri;
        image.alt = preview.name;
        body.replaceChildren(image, metaRows(preview));
    }
    else if (preview.preview_kind === "text" && preview.text !== undefined) {
        const code = document.createElement("pre");
        code.className = "text-preview";
        code.textContent = preview.text;
        body.replaceChildren(code, preview.truncated ? text("p", "preview-note", "Preview truncated.") : metaRows(preview));
    }
    else {
        body.replaceChildren(text("div", "empty-state", "Preview is not available for this file type."), metaRows(preview));
    }
}
async function loadDirectory(path, pushHistory = true) {
    const requestId = ++directoryRequest;
    const previousPath = state.currentPath;
    ++previewRequest;
    state.loading = true;
    state.selected = null;
    state.preview = null;
    renderEntries();
    renderPreview();
    try {
        const payload = await apiGet("./api/list", { path });
        if (requestId !== directoryRequest)
            return;
        if (pushHistory && previousPath && previousPath !== payload.path)
            state.history.push(previousPath);
        state.currentPath = payload.path;
        state.parentPath = payload.parent || null;
        state.entries = payload.entries;
        truncated = payload.truncated;
        state.loading = false;
        renderLocations();
        renderBreadcrumbs();
        renderEntries();
    }
    catch (error) {
        if (requestId !== directoryRequest)
            return;
        state.loading = false;
        renderEntries();
        showError(error instanceof Error ? error.message : String(error));
    }
}
async function selectEntry(entry) {
    const requestId = ++previewRequest;
    state.selected = entry;
    state.preview = { ...entry, success: true };
    renderEntries();
    renderPreview();
    const dialog = byId("preview-dialog");
    if (window.matchMedia("(max-width: 1200px)").matches) {
        dialog.append(byId("preview-panel"));
        if (!dialog.open)
            dialog.showModal();
    }
    try {
        const preview = await apiGet("./api/preview", { path: entry.path });
        if (requestId !== previewRequest)
            return;
        state.preview = preview;
        renderPreview();
    }
    catch (error) {
        showError(error instanceof Error ? error.message : String(error));
    }
}
async function loadLocations() {
    const payload = await apiGet("./api/locations");
    state.locations = payload.locations;
    renderLocations();
    await loadDirectory(payload.default_path, false);
}
function bindEvents() {
    byId("settings-button").addEventListener("click", () => byId("appearance-dialog").showModal());
    document.querySelectorAll('input[name="skin"]').forEach((input) => input.addEventListener("change", () => {
        appearance.skin = input.value;
        appearance.accent = skinAccents[appearance.skin];
        applyAppearance();
    }));
    byId("accent-input").addEventListener("input", (event) => { appearance.accent = event.target.value; applyAppearance(); });
    byId("density-select").addEventListener("change", (event) => { appearance.density = event.target.value; applyAppearance(); });
    byId("appearance-reset").addEventListener("click", () => { appearance = { skin: "professional", accent: skinAccents.professional, density: "comfortable" }; applyAppearance(); });
    byId("sort-select").addEventListener("change", (event) => { sortMode = event.target.value; renderEntries(); });
    byId("show-hidden").addEventListener("change", (event) => { showHidden = event.target.checked; renderEntries(); });
    const previewDialog = byId("preview-dialog");
    const previewPanel = byId("preview-panel");
    const previewParent = previewPanel.parentElement;
    byId("preview-close").addEventListener("click", () => previewDialog.close());
    previewDialog.addEventListener("close", () => previewParent.append(previewPanel));
    window.matchMedia("(max-width: 1200px)").addEventListener("change", (event) => { if (!event.matches && previewDialog.open)
        previewDialog.close(); });
    byId("file-search").addEventListener("input", (event) => {
        state.query = event.target.value;
        renderEntries();
    });
    byId("go-button").addEventListener("click", () => {
        void loadDirectory(byId("path-input").value);
    });
    byId("path-input").addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            void loadDirectory(event.target.value);
        }
    });
    byId("back-button").addEventListener("click", () => {
        const previous = state.history.pop();
        if (previous) {
            void loadDirectory(previous, false);
        }
    });
    byId("up-button").addEventListener("click", () => {
        if (state.parentPath) {
            void loadDirectory(state.parentPath);
        }
    });
    byId("refresh-button").addEventListener("click", () => {
        void apiGet("./api/locations").then((payload) => { state.locations = payload.locations; renderLocations(); }).catch((error) => showError(String(error)));
        void loadDirectory(state.currentPath, false);
    });
    byId("copy-path-button").addEventListener("click", async () => {
        const path = state.selected?.path || state.currentPath;
        try {
            await navigator.clipboard.writeText(path);
            showToast("Path copied.");
        }
        catch {
            showToast(path);
        }
    });
    for (const [buttonId, view] of [
        ["list-view", "list"],
        ["grid-view", "grid"],
    ]) {
        byId(buttonId).addEventListener("click", () => {
            state.view = view;
            byId("list-view").classList.toggle("active", view === "list");
            byId("grid-view").classList.toggle("active", view === "grid");
            byId("list-view").setAttribute("aria-pressed", String(view === "list"));
            byId("grid-view").setAttribute("aria-pressed", String(view === "grid"));
            renderEntries();
        });
    }
}
document.querySelectorAll("[data-icon]").forEach((element) => element.replaceChildren(svgIcon(element.dataset.icon || "file")));
try {
    const saved = JSON.parse(localStorage.getItem(appearanceKey) || "null");
    if (saved && ["professional", "midnight", "neon"].includes(saved.skin) && /^#[0-9a-f]{6}$/i.test(saved.accent) && ["compact", "comfortable"].includes(saved.density))
        appearance = saved;
}
catch { /* Use the default when browser storage is unavailable. */ }
applyAppearance(false);
bindEvents();
renderPreview();
void loadLocations().catch((error) => {
    state.loading = false;
    renderEntries();
    showError(error instanceof Error ? error.message : String(error));
});
export {};
