import {collectDroppedFiles, relativePath} from './content-drop.js';

(function () {
    'use strict';
    const base = document.body.dataset.api;
    const $ = id => document.getElementById(id);
    let rows = [], active = null, loading = false, scanning = false, generation = 0;
    async function api(path = '', options = {}) {
        const response = await fetch(base + path, {credentials: 'same-origin', ...options});
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || body.detail || 'Unable to update your library');
        return body;
    }
    function node(tag, text, className) { const item = document.createElement(tag); item.textContent = text; if (className) item.className = className; return item; }
    function render() {
        $('items').replaceChildren();
        if (!rows.length) $('items').append(node('p', 'Your library is ready. Add files here, or save something in an AutoYou agent.'));
        rows.forEach(row => {
            const card = node('article', '', 'item');
            const sourceName = row.source.startsWith('custom_') ? 'custom agent' : row.source.replace(/_agent$/, '').replace(/_/g, ' ');
            card.append(node('p', row.kind + ' / ' + sourceName, 'kind'), node('h2', row.title), node('p', row.collection || 'Unsorted'), node('p', row.labels.join(' / '), 'labels'));
            if (row.annotation) card.append(node('p', row.annotation.slice(0, 180)));
            const actions = node('footer', '');
            const open = node('a', 'Open', 'button'); open.href = row.href || base + '/items/' + encodeURIComponent(row.id) + '/content'; open.target = '_blank'; open.rel = 'noopener';
            const edit = node('button', 'Organize'); edit.onclick = () => { active = row; $('edit-title').textContent = row.title; $('edit-collection').value = row.collection; $('edit-labels').value = row.labels.join(', '); $('edit-note').value = row.annotation; $('edit-status').textContent = ''; $('editor').showModal(); };
            actions.append(open, edit); card.append(actions); $('items').append(card);
        });
    }
    async function load(more = false) {
        const ticket = ++generation;
        const params = new URLSearchParams({q: $('search').value, kind: $('kind').value, collection: $('collection').value, offset: String(more ? rows.length : 0), limit: '60'});
        try {
            const result = await api('?' + params);
            if (ticket !== generation) return;
            const nextRows = more ? rows.concat(result.items) : result.items;
            const changed = JSON.stringify(rows) !== JSON.stringify(nextRows);
            rows = nextRows;
            $('count').textContent = result.total + ' items'; $('more').hidden = !result.has_more;
            if (document.activeElement !== $('output-root')) $('output-root').value = result.settings.output_root;
            $('protection').textContent = result.settings.encrypted_at_rest ? 'Maximus is active. New managed outputs use encrypted storage. Linked external originals remain in their source folders.' : 'Maximus is not active. Enable it in Security to encrypt new managed content at rest.';
            if (changed || !$('items').childElementCount) render();
        } catch (error) { $('status').textContent = error.message; }
    }
    async function files(input) {
        if (loading || scanning) return;
        loading = true; $('progress').hidden = false;
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
        await load();
    }
    $('files').onchange = event => { files(event.target.files); event.target.value = ''; };
    $('folder').onchange = event => { files(event.target.files); event.target.value = ''; };
    document.addEventListener('dragover', event => {
        if (Array.from(event.dataTransfer.types).includes('Files')) { event.preventDefault(); $('drop').classList.add('over'); }
    });
    document.addEventListener('dragleave', event => { if (!event.relatedTarget) $('drop').classList.remove('over'); });
    document.addEventListener('drop', async event => {
        if (!Array.from(event.dataTransfer.types).includes('Files')) return;
        event.preventDefault(); $('drop').classList.remove('over');
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
    for (const id of ['search', 'collection']) $(id).oninput = () => { clearTimeout(timer); timer = setTimeout(() => load(), 220); };
    $('kind').onchange = () => load(); $('refresh').onclick = () => load(); $('more').onclick = () => load(true);
    $('index-existing').onclick = async () => {
        $('index-existing').disabled = true; $('status').textContent = 'Indexing existing agent catalogs...';
        try { const result = await api('/refresh-existing', {method: 'POST'}); $('status').textContent = result.indexed + ' items indexed. ' + result.errors.join(' / '); await load(); }
        catch (error) { $('status').textContent = error.message; }
        finally { $('index-existing').disabled = false; }
    };
    $('settings-toggle').onclick = () => { $('storage').hidden = !$('storage').hidden; };
    $('save-root').onclick = async () => { try { const result = await api('/settings', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({output_root: $('output-root').value})}); $('status').textContent = result.message; } catch (error) { $('status').textContent = error.message; } };
    $('suggest').onclick = () => { if (active) $('edit-labels').value = active.suggested_labels.join(', '); };
    $('edit-form').onsubmit = async event => {
        event.preventDefault(); if (!active) return;
        try { await api('/items/' + active.id, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({labels: $('edit-labels').value.split(',').map(x => x.trim()).filter(Boolean), annotation: $('edit-note').value, collection: $('edit-collection').value, expected_revision: active.revision})}); $('editor').close(); await load(); }
        catch (error) { $('edit-status').textContent = error.message; }
    };
    document.addEventListener('visibilitychange', () => { if (!document.hidden && !$('editor').open && !loading) load(); });
    setInterval(() => { if (!document.hidden && !$('editor').open && !loading && $('storage').hidden && rows.length <= 60) load(); }, 15000);
    load();
}());
