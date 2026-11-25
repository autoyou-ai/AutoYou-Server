import {collectDroppedFiles, relativePath} from './content-drop.js';

(function () {
    'use strict';
    const base = location.origin + location.pathname.replace(/\/view\/?$/, '');
    const $ = id => document.getElementById(id);
    let rows = [], active = null, loading = false, scanning = false, generation = 0;
    let syncRequestedAt = 0, syncBaseline = 0, lastSync = 0, renderedFilters = '';
    const sourceNames = {
        notes_agent: 'Notes', page_agent: 'Pages', media_generation_agent: 'Media Generation',
        music_agent: 'Audio', media_agent: 'TV', files_agent: 'Files',
        voice_training_agent: 'Voice Training', location_agent: 'Locations',
    };
    const dateFormat = new Intl.DateTimeFormat(undefined, {dateStyle: 'medium'});
    function sourceName(source) {
        return sourceNames[source] || source.replace(/_agent$/, '').replace(/_/g, ' ');
    }
    function itemLink(row) {
        if (row.has_file) return base + '/items/' + encodeURIComponent(row.id) + '/content';
        if (!row.href || !/^\d+$/.test(row.source_id)) return row.href;
        if (row.source === 'notes_agent') return row.href + '#note-' + row.source_id;
        if (row.source === 'page_agent') return row.href + '#item-' + row.source_id;
        return row.href;
    }
    async function api(path = '', options = {}) {
        const response = await fetch(base + path, {credentials: 'same-origin', ...options});
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || body.detail || 'Unable to update your library');
        return body;
    }
    function node(tag, text, className) { const item = document.createElement(tag); item.textContent = text; if (className) item.className = className; return item; }
    function render() {
        $('items').replaceChildren();
        if (!rows.length) {
            const filtered = ['search', 'kind', 'source', 'collection'].some(id => $(id).value);
            $('items').append(node('p', filtered ? 'No content matches these filters.' : 'No content yet. Sync from agents or add files.', 'empty'));
        }
        rows.forEach(row => {
            const item = node('article', '', 'item');
            const description = node('div', '');
            const source = sourceName(row.source);
            const kind = row.kind[0].toUpperCase() + row.kind.slice(1);
            const updated = row.updated ? dateFormat.format(new Date(row.updated * 1000)) : '';
            const details = [kind, source, row.collection || row.source_collection, updated].filter(Boolean);
            description.append(node('h2', row.title), node('p', details.join(' · '), 'details'));
            if (row.annotation) description.append(node('p', row.annotation.slice(0, 180), 'annotation'));
            const actions = node('div', '', 'actions');
            if (row.has_file || row.href) {
                const open = node('a', row.has_file ? 'Open file' : 'Open in ' + source, 'button');
                open.href = itemLink(row);
                open.target = '_top';
                open.setAttribute('aria-label', open.textContent + ': ' + row.title);
                actions.append(open);
            }
            const edit = node('button', 'Organize');
            edit.setAttribute('aria-label', 'Organize ' + row.title);
            edit.onclick = () => {
                active = row;
                $('edit-title').textContent = row.title;
                $('edit-collection').value = row.collection;
                $('edit-labels').value = row.labels.join(', ');
                $('edit-note').value = row.annotation;
                $('edit-status').textContent = '';
                $('editor').showModal();
            };
            actions.append(edit);
            item.append(description, actions);
            $('items').append(item);
        });
    }
    async function load(more = false) {
        const ticket = ++generation;
        const params = new URLSearchParams({
            q: $('search').value.trim(), kind: $('kind').value, source: $('source').value,
            collection: $('collection').value, offset: String(more ? rows.length : 0), limit: '60',
        });
        try {
            const result = await api('?' + params);
            if (ticket !== generation) return;
            const nextRows = more ? rows.concat(result.items) : result.items;
            const filters = params.toString();
            const changed = filters !== renderedFilters || JSON.stringify(rows) !== JSON.stringify(nextRows);
            renderedFilters = filters;
            rows = nextRows;
            $('count').textContent = result.total + (result.total === 1 ? ' item' : ' items');
            $('more').hidden = !result.has_more;
            if (document.activeElement !== $('output-root')) $('output-root').value = result.settings.output_root;
            $('protection').textContent = result.settings.encrypted_at_rest ? 'Maximus is active. New managed outputs use encrypted storage. Linked external originals remain in their source folders.' : 'Maximus is not active. Enable it in Security to encrypt new managed content at rest.';
            if (changed || !$('items').childElementCount) render();
        } catch (error) {
            if (ticket !== generation) return;
            $('count').textContent = 'Unable to load content';
            $('status').textContent = error.message;
        }
    }
    function setOptions(select, options) {
        if (select === document.activeElement) return;
        const previous = select.value;
        select.replaceChildren(select.options[0]);
        for (const [value, label] of options) select.add(new Option(label, value));
        if (previous && !options.some(([value]) => value === previous)) select.add(new Option(previous, previous));
        select.value = previous;
    }
    async function loadActivity() {
        const result = await api('/activity');
        const sources = result.sources.map(({source, count}) => [source, sourceName(source) + ' (' + count + ')']);
        setOptions($('source'), sources);
        setOptions($('collection'), result.collections.map(name => [name, name]));
        $('collections').replaceChildren(...result.collections.map(name => new Option(name, name)));
        const worker = result.worker;
        lastSync = worker.last_scan || lastSync;
        if (syncRequestedAt && lastSync > syncBaseline) syncRequestedAt = 0;
        const errors = worker.scan_errors || [];
        $('sync-status').textContent = worker.state === 'indexing' || syncRequestedAt
            ? 'Syncing agent content…'
            : errors.length
                ? errors.join(' · ')
                : lastSync ? 'Last agent sync: ' + dateFormat.format(new Date(lastSync * 1000)) : '';
    }
    async function files(input) {
        if (loading || scanning) return;
        loading = true; $('progress').hidden = false; $('add').open = false;
        const all = Array.from(input), failures = [];
        for (let i = 0; i < all.length; i++) {
            const file = all[i]; $('status').textContent = 'Adding ' + file.name + ' (' + (i + 1) + '/' + all.length + ')';
            try {
                const key = 'autoyou-content-upload:' + JSON.stringify([location.pathname, relativePath(file), file.size, file.lastModified]);
                let upload = null, saved = '';
                try { saved = sessionStorage.getItem(key) || ''; } catch (_) {}
                if (saved) { try { upload = await api('/upload-sessions/' + saved); } catch (_) { /* A fresh session is safe; the original remains unpublished. */ } }
                if (!upload) upload = await api('/upload-sessions', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({filename: file.name, size: file.size})});
                try { sessionStorage.setItem(key, upload.id); } catch (_) {}
                while (upload.received < file.size) {
                    upload = await api('/upload-sessions/' + upload.id + '?offset=' + upload.received, {method: 'PUT', headers: {'Content-Type': 'application/octet-stream'}, body: file.slice(upload.received, upload.received + upload.chunk_bytes)});
                    $('progress').value = (i + upload.received / file.size) / all.length;
                }
                const result = await api('/upload-sessions/' + upload.id + '/complete', {method: 'POST'});
                const row = result.item;
                try { sessionStorage.removeItem(key); } catch (_) {}
                const relative = relativePath(file);
                if (relative.includes('/')) await api('/items/' + row.id, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({expected_revision: row.revision, labels: row.labels, annotation: '', collection: relative.split('/').slice(0, -1).join('/').slice(0, 100)})});
            } catch (error) { failures.push(file.name + ': ' + error.message); }
            $('progress').value = (i + 1) / all.length;
        }
        loading = false; $('progress').hidden = true; $('status').textContent = failures.length ? failures.join(' / ') + ' Select the same files to resume.' : 'Added ' + all.length + ' files. Ready to organize.';
        await Promise.all([load(), loadActivity().catch(() => {})]);
    }
    $('files').onchange = event => { files(event.target.files); event.target.value = ''; };
    $('folder').onchange = event => { files(event.target.files); event.target.value = ''; };
    document.addEventListener('dragover', event => {
        if (Array.from(event.dataTransfer.types).includes('Files')) { event.preventDefault(); $('content-drop-zone').classList.add('over'); }
    });
    document.addEventListener('dragleave', event => { if (!event.relatedTarget) $('content-drop-zone').classList.remove('over'); });
    document.addEventListener('drop', async event => {
        if (!Array.from(event.dataTransfer.types).includes('Files')) return;
        event.preventDefault(); $('content-drop-zone').classList.remove('over');
        if (loading || scanning) { $('status').textContent = 'Let the current import finish before adding more files.'; return; }
        scanning = true; $('status').textContent = 'Reading your folders...';
        try {
            const selected = await collectDroppedFiles(event.dataTransfer, count => { $('status').textContent = 'Found ' + count + ' files...'; });
            scanning = false;
            if (selected.length) await files(selected);
            else $('status').textContent = 'No files found. Choose a folder containing files.';
        } catch (error) { $('status').textContent = error.message; }
        finally { scanning = false; }
    });
    let timer;
    $('search').oninput = () => {
        clearTimeout(timer);
        generation++;
        $('more').hidden = true;
        timer = setTimeout(() => load(), 220);
    };
    for (const id of ['kind', 'source', 'collection']) $(id).onchange = () => { clearTimeout(timer); load(); };
    $('more').onclick = async () => {
        $('more').disabled = true;
        try { await load(true); } finally { $('more').disabled = false; }
    };
    $('index-existing').onclick = async () => {
        $('index-existing').disabled = true;
        $('status').textContent = '';
        syncBaseline = lastSync;
        try {
            const result = await api('/refresh-existing', {method: 'POST'});
            if (result.queued) {
                syncRequestedAt = Date.now() / 1000;
                $('sync-status').textContent = 'Syncing agent content…';
            } else {
                $('status').textContent = result.errors.join(' · ') || 'Agent content synced.';
            }
            await Promise.all([load(), loadActivity()]);
        } catch (error) { $('status').textContent = error.message; }
        finally { $('index-existing').disabled = false; }
    };
    $('settings-toggle').onclick = () => {
        $('storage').hidden = !$('storage').hidden;
        $('settings-toggle').setAttribute('aria-expanded', String(!$('storage').hidden));
    };
    $('save-root').onclick = async () => { try { const result = await api('/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({output_root: $('output-root').value})}); $('status').textContent = result.message; } catch (error) { $('status').textContent = error.message; } };
    $('suggest').onclick = () => { if (active) $('edit-labels').value = active.suggested_labels.join(', '); };
    $('edit-form').onsubmit = async event => {
        event.preventDefault(); if (!active) return;
        try {
            await api('/items/' + active.id, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({labels: $('edit-labels').value.split(',').map(x => x.trim()).filter(Boolean), annotation: $('edit-note').value, collection: $('edit-collection').value, expected_revision: active.revision})});
            $('editor').close();
            await Promise.all([load(), loadActivity().catch(() => {})]);
        }
        catch (error) { $('edit-status').textContent = error.message; }
    };
    function refresh() {
        if (document.hidden || $('editor').open || loading || !$('storage').hidden || rows.length > 60) return;
        if (syncRequestedAt && Date.now() / 1000 - syncRequestedAt > 300) {
            syncRequestedAt = 0;
            $('status').textContent = 'The agent sync has not completed yet. You can refresh this page to check again.';
        }
        load();
        loadActivity().catch(() => {});
    }
    document.addEventListener('visibilitychange', refresh);
    setInterval(refresh, 15000);
    refresh();
}());
