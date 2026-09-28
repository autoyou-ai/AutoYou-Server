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
    return state.entries.filter(matchesQuery);
}
function isInLocation(path, location) {
    const current = path.toLowerCase();
    const base = location.toLowerCase();
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
    root.replaceChildren(...state.locations.map((location) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "location-button";
        button.classList.toggle("active", Boolean(state.currentPath) && isInLocation(state.currentPath, location.path));
        button.append(text("span", `location-icon ${location.kind}`, location.kind === "drive" ? "DRV" : "DIR"), text("span", "location-label", location.label), text("span", "location-path", location.path));
        button.addEventListener("click", () => {
            void loadDirectory(location.path);
        });
        return button;
    }));
}
function renderEntry(entry) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = state.view === "grid" ? "entry-card" : "entry-row";
    button.classList.toggle("active", state.selected?.path === entry.path);
    const main = text("span", "entry-main", "");
    main.append(text("span", `file-icon ${entry.kind}`, iconLabel(entry)), text("span", "entry-name", entry.name));
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
    byId("path-input").value = state.currentPath;
    byId("back-button").disabled = state.history.length === 0;
    byId("up-button").disabled = !state.parentPath;
    if (state.loading) {
        root.replaceChildren(text("div", "empty-state", "Loading folder..."));
        return;
    }
    const rows = filteredEntries();
    byId("status-line").textContent = `${rows.length} item${rows.length === 1 ? "" : "s"}${state.query ? " matched" : ""}`;
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
        title.textContent = state.currentPath || "Workspace";
        meta.textContent = "Current folder";
        body.replaceChildren(text("div", "empty-state", "Select a file to preview it."));
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
    if (pushHistory && state.currentPath && state.currentPath !== path) {
        state.history.push(state.currentPath);
    }
    state.loading = true;
    state.selected = null;
    state.preview = null;
    renderEntries();
    renderPreview();
    try {
        const payload = await apiGet("./api/list", { path });
        state.currentPath = payload.path;
        state.parentPath = payload.parent || null;
        state.entries = payload.entries;
        state.loading = false;
        renderLocations();
        renderEntries();
    }
    catch (error) {
        state.loading = false;
        renderEntries();
        showError(error instanceof Error ? error.message : String(error));
    }
}
async function selectEntry(entry) {
    state.selected = entry;
    state.preview = { ...entry, success: true };
    renderEntries();
    renderPreview();
    try {
        state.preview = await apiGet("./api/preview", { path: entry.path });
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
            renderEntries();
        });
    }
}
bindEvents();
renderPreview();
void loadLocations().catch((error) => {
    state.loading = false;
    renderEntries();
    showError(error instanceof Error ? error.message : String(error));
});
export {};
