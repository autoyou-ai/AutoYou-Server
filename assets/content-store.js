/* My Content: the cached index behind the screen.
 *
 * Pure logic with no DOM, so it runs the same in a browser and under `node --test`.
 * The screen walks the server's change feed once, keeps every row here, and from
 * then on only applies what changed. Filtering, searching, sorting and grouping are
 * answered from memory, so a slow or remote connection is only used for changes.
 */
(function (root, factory) {
    if (typeof module === "object" && module.exports) { module.exports = factory(); }
    else { root.AutoYouContentStore = factory(); }
}(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    var KINDS = [
        ["note", "Notes"], ["page", "Pages"], ["image", "Images"], ["video", "Video"], ["audio", "Audio"],
        ["voice", "Voice"], ["file", "Files"], ["location", "Locations"], ["agent", "Agent records"]
    ];
    var KIND_LABEL = {};
    KINDS.forEach(function (pair) { KIND_LABEL[pair[0]] = pair[1]; });

    // Lower case, no accents, single spaces: "Café  Menu" and "cafe menu" are the same text.
    function fold(value) {
        var text = String(value == null ? "" : value).toLowerCase();
        if (text.normalize) { text = text.normalize("NFD").replace(/[̀-ͯ]/g, ""); }
        return text.replace(/\s+/g, " ").trim();
    }

    function tokens(query) {
        var folded = fold(query);
        return folded ? folded.split(" ") : [];
    }

    // Everything searchable about a row, folded once and kept on the row.
    function haystack(row, agentName) {
        if (row._hay === undefined || row._hayAgent !== agentName || row._hayRevision !== row.revision) {
            row._hay = fold([row.title, (row.labels || []).concat(row.source_labels || []).join(" "), row.annotation, row.collection || row.source_collection, agentName, row.kind].join(" "));
            row._hayAgent = agentName;
            row._hayRevision = row.revision;
        }
        return row._hay;
    }

    function collectionOf(row) { return row.collection || row.source_collection || ""; }

    function compare(sort) {
        if (sort === "title") {
            return function (a, b) { return fold(a.title).localeCompare(fold(b.title)) || (a.id < b.id ? -1 : 1); };
        }
        if (sort === "size") {
            return function (a, b) { return (b.byte_size || 0) - (a.byte_size || 0) || (a.id < b.id ? -1 : 1); };
        }
        return function (a, b) { return (b.updated || 0) - (a.updated || 0) || (a.id < b.id ? -1 : 1); };
    }

    // A link or media address from the server or a host page is only ever followed when it stays on
    // this site (a path), is a fragment, a relative path (the public demo), or an in-page blob.
    function safeUrl(value) {
        var text = String(value == null ? "" : value).trim();
        if (/^blob:/i.test(text) || /^#/.test(text) || /^\.{1,2}\//.test(text) || /^\/(?![\/\\])/.test(text)) { return text; }
        return "";
    }

    function formatBytes(bytes) {
        var value = Math.max(0, Number(bytes) || 0), units = ["B", "KB", "MB", "GB", "TB"], index = 0;
        while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
        return (index === 0 ? String(Math.round(value)) : String(Math.round(value * 10) / 10)) + " " + units[index];
    }

    function relativeTime(seconds, nowSeconds) {
        if (!seconds) { return ""; }
        var now = nowSeconds == null ? Date.now() / 1000 : nowSeconds, delta = Math.max(0, now - seconds);
        if (delta < 60) { return "Just now"; }
        if (delta < 3600) { var m = Math.floor(delta / 60); return m + (m === 1 ? " minute ago" : " minutes ago"); }
        if (delta < 86400) { var h = Math.floor(delta / 3600); return h + (h === 1 ? " hour ago" : " hours ago"); }
        if (delta < 172800) { return "Yesterday"; }
        if (delta < 604800) { return Math.floor(delta / 86400) + " days ago"; }
        return new Date(seconds * 1000).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    }

    function createStore() {
        var rows = new Map();
        var store = {
            cursor: 0,
            revision: 0,
            walkComplete: false,
            summary: null,
            agents: new Map(),

            size: function () { return rows.size; },
            get: function (id) { return rows.get(id) || null; },
            all: function () { return Array.from(rows.values()); },

            reset: function () {
                rows.clear();
                store.cursor = 0;
                store.revision = 0;
                store.walkComplete = false;
            },

            setSummary: function (summary) {
                var changedScope = !!store.summary && store.summary.access_scope !== summary.access_scope;
                if (changedScope) { store.reset(); }
                store.summary = summary;
                store.agents = new Map();
                (summary.agents || []).forEach(function (agent) { store.agents.set(agent.source, agent); });
                return changedScope;
            },

            agent: function (source) {
                return store.agents.get(source) || { source: source, name: source.replace(/^custom_/, "").replace(/_agent$/, "").replace(/_/g, " "), icon: "agent", accent: "" };
            },

            // Merge one page of the change feed. Safe to apply twice or out of order:
            // a row only replaces an older revision of itself, a tombstone only removes older rows.
            applyChanges: function (payload) {
                var result = { added: 0, updated: 0, removed: 0 };
                (payload.items || []).forEach(function (item) {
                    var current = rows.get(item.id);
                    if (current && current.revision > item.revision) { return; }
                    if (item.deleted) {
                        if (current) { rows.delete(item.id); result.removed += 1; }
                        return;
                    }
                    rows.set(item.id, item);
                    if (current) { result.updated += 1; } else { result.added += 1; }
                });
                if (typeof payload.cursor === "number" && payload.cursor > store.cursor) { store.cursor = payload.cursor; }
                if (typeof payload.revision === "number" && payload.revision > store.revision) { store.revision = payload.revision; }
                if (payload.has_more === false) { store.walkComplete = true; }
                return result;
            },

            // Local rows that satisfy `filter`. `skip` leaves one dimension out so
            // facet counts can show what each choice would give.
            query: function (filter, skip) {
                filter = filter || {};
                var words = skip === "text" ? [] : tokens(filter.text);
                var out = [];
                rows.forEach(function (row) {
                    if (skip !== "kind" && filter.kind && row.kind !== filter.kind) { return; }
                    if (skip !== "source" && filter.source && row.source !== filter.source) { return; }
                    if (skip !== "collection" && filter.collection && collectionOf(row) !== filter.collection) { return; }
                    if (words.length) {
                        var text = haystack(row, store.agent(row.source).name);
                        for (var i = 0; i < words.length; i += 1) { if (text.indexOf(words[i]) === -1) { return; } }
                    }
                    out.push(row);
                });
                return out.sort(compare(filter.sort));
            },

            facets: function (filter) {
                var kinds = {}, sources = {}, collections = {};
                store.query(filter, "kind").forEach(function (row) { kinds[row.kind] = (kinds[row.kind] || 0) + 1; });
                store.query(filter, "source").forEach(function (row) { sources[row.source] = (sources[row.source] || 0) + 1; });
                store.query(filter, "collection").forEach(function (row) {
                    var name = collectionOf(row);
                    if (name) { collections[name] = (collections[name] || 0) + 1; }
                });
                return { kinds: kinds, sources: sources, collections: collections };
            },

            // Rows the server found for words this index cannot see (note bodies). They are
            // already in the index; this only says which of them to add to a local answer.
            withServerMatches: function (local, serverRows) {
                var seen = new Set(local.map(function (row) { return row.id; }));
                var extra = [];
                (serverRows || []).forEach(function (row) {
                    var known = rows.get(row.id) || row;
                    if (!seen.has(known.id)) { seen.add(known.id); extra.push(known); }
                });
                return local.concat(extra);
            },

            groups: function (list, per) {
                var by = new Map();
                list.forEach(function (row) {
                    if (!by.has(row.source)) { by.set(row.source, { source: row.source, count: 0, items: [] }); }
                    var group = by.get(row.source);
                    group.count += 1;
                    if (group.items.length < per) { group.items.push(row); }
                });
                return Array.from(by.values()).sort(function (a, b) { return (b.items[0].updated || 0) - (a.items[0].updated || 0); });
            },

            // A short string that changes whenever a list would render differently.
            signature: function (list) {
                var parts = [list.length];
                for (var i = 0; i < list.length; i += 1) { parts.push(list[i].id + ":" + list[i].revision); }
                return parts.join("|");
            }
        };
        return store;
    }

    return {
        createStore: createStore, fold: fold, tokens: tokens, formatBytes: formatBytes, relativeTime: relativeTime, safeUrl: safeUrl,
        compare: compare, collectionOf: collectionOf, KINDS: KINDS, KIND_LABEL: KIND_LABEL
    };
}));
