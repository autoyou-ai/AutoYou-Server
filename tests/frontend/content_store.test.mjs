import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

// The store is plain browser JavaScript, so run it the way a page does: in a context whose
// global is `self`, with no module system.
const source = await readFile(new URL('../../assets/content-store.js', import.meta.url), 'utf8');
const sandbox = {URLSearchParams};
sandbox.self = sandbox;
vm.runInNewContext(source, sandbox);
const {createStore, fold, tokens, formatBytes, relativeTime, safeUrl} = sandbox.AutoYouContentStore;

// The store lives in another realm, so compare plain copies of what it returns.
const plain = value => JSON.parse(JSON.stringify(value));
const eq = (actual, expected, message) => assert.deepEqual(plain(actual), expected, message);

const row = (id, over = {}) => ({
    id, source: 'notes_agent', source_id: id, title: 'Synthetic ' + id, kind: 'note', href: '', labels: [], annotation: '',
    collection: '', source_collection: '', revision: 1, updated: 1000, organization_state: 'ready', byte_size: 0, has_file: false, deleted: 0, ...over,
});
const tomb = (id, revision) => ({id, source: 'notes_agent', deleted: 1, revision});
const page = (items, cursor, extra = {}) => ({items, cursor, revision: cursor, has_more: false, ...extra});

test('content deep links round-trip filters and the selected item without changing host parameters', () => {
    const {readRoute, routeSearch} = sandbox.AutoYouContentStore;
    const filter = {text: 'synthetic garden', kind: 'note', source: 'custom_garden_a1b2c3', collection: 'Spring & Summer', sort: 'title'};
    const search = routeSearch('?screen=content&role=synthetic-viewer', filter, 'list', 'synthetic-item');
    eq(readRoute(search), {filter, view: 'list', item: 'synthetic-item'});
    const params = new URLSearchParams(search);
    assert.equal(params.get('screen'), 'content');
    assert.equal(params.get('role'), 'synthetic-viewer');
    assert.equal(routeSearch(search, {text: '', kind: '', source: '', collection: '', sort: 'updated'}, 'agents', ''), '?screen=content&role=synthetic-viewer');
});

test('deep links reject invalid sources, kinds and item IDs and bound user text', () => {
    const {readRoute} = sandbox.AutoYouContentStore;
    const state = readRoute('?content_source=../escape&content_kind=__proto__&content_sort=invalid&content_item=%3Cscript%3E&content_q=' + 'x'.repeat(500));
    eq({...state.filter, text: ''}, {text: '', source: '', kind: '', collection: '', sort: 'updated'});
    assert.equal(state.filter.text.length, 300);
    assert.equal(state.item, '');
    assert.equal(readRoute('?content_source=custom_garden_a1b2c3').filter.source, 'custom_garden_a1b2c3');
});

test('a permission scope change clears old rows and restarts the cursor even without a data revision change', () => {
    const store = createStore();
    assert.equal(store.setSummary({agents: [], access_scope: 'synthetic-notes-unlocked'}), false);
    store.applyChanges(page([row('private-note')], 8));
    assert.equal(store.setSummary({agents: [], access_scope: 'synthetic-notes-unlocked'}), false);
    assert.equal(store.size(), 1);
    assert.equal(store.setSummary({agents: [], access_scope: 'synthetic-notes-locked'}), true);
    assert.equal(store.size(), 0);
    assert.equal(store.cursor, 0);
    assert.equal(store.walkComplete, false);
});

test('applying a change feed adds, updates and removes rows and is safe to repeat', () => {
    const store = createStore();
    eq(store.applyChanges(page([row('a'), row('b')], 2)), {added: 2, updated: 0, removed: 0});
    eq(store.applyChanges(page([row('a', {title: 'Renamed', revision: 3})], 3)), {added: 0, updated: 1, removed: 0});
    assert.equal(store.get('a').title, 'Renamed');
    eq(store.applyChanges(page([tomb('b', 4)], 4)), {added: 0, updated: 0, removed: 1});
    assert.equal(store.size(), 1);
    // Replaying an old page, or a removal for a row it never had, changes nothing.
    eq(store.applyChanges(page([row('a', {title: 'Stale', revision: 1}), tomb('ghost', 9)], 2)), {added: 0, updated: 0, removed: 0});
    assert.equal(store.get('a').title, 'Renamed');
    assert.equal(store.cursor, 4, 'the cursor never moves backwards');
});

test('an older tombstone cannot remove a newer row', () => {
    const store = createStore();
    store.applyChanges(page([row('a', {revision: 9})], 9));
    store.applyChanges(page([tomb('a', 5)], 9));
    assert.equal(store.size(), 1);
});

test('the walk is complete only once the server says there is nothing more', () => {
    const store = createStore();
    store.applyChanges(page([row('a')], 1, {has_more: true}));
    assert.equal(store.walkComplete, false);
    store.applyChanges(page([row('b')], 2, {has_more: false}));
    assert.equal(store.walkComplete, true);
    store.reset();
    eq([store.size(), store.cursor, store.walkComplete], [0, 0, false]);
});

test('search ignores case, accents and word order, and every word must match', () => {
    const store = createStore();
    store.setSummary({agents: [{source: 'notes_agent', name: 'Notes'}, {source: 'page_agent', name: 'Pages'}]});
    store.applyChanges(page([
        row('1', {title: 'Café menu draft', labels: ['restaurant'], updated: 3}),
        row('2', {title: 'Garden plan', annotation: 'Tomatoes by the cafe', updated: 2}),
        row('3', {title: 'Saved article', source: 'page_agent', kind: 'page', updated: 1}),
    ], 3));
    eq(store.query({text: 'CAFE'}).map(r => r.id), ['1', '2']);
    eq(store.query({text: 'menu cafe'}).map(r => r.id), ['1']);
    eq(store.query({text: 'pages'}).map(r => r.id), ['3'], 'the agent name is searchable');
    eq(store.query({text: 'restaurant'}).map(r => r.id), ['1'], 'labels are searchable');
    eq(store.query({text: 'nothing like this'}), []);
    assert.equal(fold('  Crème   Brûlée '), 'creme brulee');
    eq(tokens('  A  b '), ['a', 'b']);
});

test('filters combine, and the collection falls back to the collection the agent supplied', () => {
    const store = createStore();
    store.applyChanges(page([
        row('1', {kind: 'note', collection: 'Studio'}),
        row('2', {kind: 'note', source_collection: 'Studio'}),
        row('3', {kind: 'image', source: 'media_generation_agent', collection: 'Studio'}),
        row('4', {kind: 'note', collection: 'Home'}),
    ], 4));
    eq(store.query({collection: 'Studio', kind: 'note'}).map(r => r.id).sort(), ['1', '2']);
    eq(store.query({source: 'media_generation_agent'}).map(r => r.id), ['3']);
});

test('facet counts include body-search matches once and still respect the other filters', () => {
    const store = createStore();
    const note = row('note', {title: 'Microphone checklist', source_collection: 'Studio'});
    const page = row('page', {title: 'Recording guide', kind: 'page', source: 'page_agent', collection: 'References'});
    store.applyChanges({items: [note, page], cursor: 2, revision: 2, has_more: false});
    eq(store.facets({text: 'gain staging'}, [note, note, page]), {
        kinds: {note: 1, page: 1}, sources: {notes_agent: 1, page_agent: 1}, collections: {Studio: 1, References: 1},
    });
    eq(store.facets({text: 'gain staging', collection: 'Studio'}, [note, page]).kinds, {note: 1});
});

test('sorting is stable by recency, name or size', () => {
    const store = createStore();
    store.applyChanges(page([
        row('1', {title: 'beta', updated: 10, byte_size: 5}),
        row('2', {title: 'Alpha', updated: 30, byte_size: 50}),
        row('3', {title: 'charlie', updated: 20, byte_size: 500}),
    ], 3));
    eq(store.query({}).map(r => r.id), ['2', '3', '1']);
    eq(store.query({sort: 'title'}).map(r => r.id), ['2', '1', '3']);
    eq(store.query({sort: 'size'}).map(r => r.id), ['3', '2', '1']);
});

test('facet counts show what each choice would give, ignoring that choice itself', () => {
    const store = createStore();
    store.applyChanges(page([
        row('1', {kind: 'note', collection: 'Studio'}),
        row('2', {kind: 'image', source: 'media_generation_agent', collection: 'Studio'}),
        row('3', {kind: 'image', source: 'media_generation_agent', collection: 'Home'}),
    ], 3));
    const facets = store.facets({kind: 'image', collection: 'Studio'});
    eq(facets.kinds, {note: 1, image: 1}, 'kind counts ignore the kind filter');
    eq(facets.collections, {Studio: 1, Home: 1}, 'collection counts ignore the collection filter');
    eq(facets.sources, {media_generation_agent: 1}, 'agent counts respect the other filters');
});

test('grouping keeps the newest few per agent and orders agents by their latest item', () => {
    const store = createStore();
    const rows = [];
    for (let i = 0; i < 5; i += 1) rows.push(row('n' + i, {updated: 100 + i}));
    rows.push(row('p0', {source: 'page_agent', kind: 'page', updated: 500}));
    store.applyChanges(page(rows, 6));
    const groups = store.groups(store.query({}), 3);
    eq(groups.map(g => [g.source, g.count, g.items.length]), [['page_agent', 1, 1], ['notes_agent', 5, 3]]);
    eq(groups[1].items.map(r => r.id), ['n4', 'n3', 'n2']);
});

test('server matches for words only a note body holds are added once, never duplicated', () => {
    const store = createStore();
    store.applyChanges(page([row('1', {title: 'Planning'}), row('2', {title: 'Shopping'})], 2));
    const local = store.query({text: 'planning'});
    const merged = store.withServerMatches(local, [row('1'), row('2', {title: 'Shopping'}), row('2')]);
    eq(merged.map(r => r.id), ['1', '2']);
});

test('an unknown agent still gets a readable name', () => {
    const store = createStore();
    assert.equal(store.agent('custom_garden_planner').name, 'garden planner');
    store.setSummary({agents: [{source: 'custom_garden_planner', name: 'Garden Planner', accent: '#22aa66'}]});
    assert.equal(store.agent('custom_garden_planner').name, 'Garden Planner');
});

test('the signature changes when a list would render differently and not otherwise', () => {
    const store = createStore();
    store.applyChanges(page([row('a'), row('b')], 2));
    const before = store.signature(store.query({}));
    assert.equal(store.signature(store.query({})), before);
    store.applyChanges(page([row('a', {revision: 5, title: 'Edited'})], 5));
    assert.notEqual(store.signature(store.query({})), before);
});

test('byte sizes and relative times read naturally', () => {
    assert.equal(formatBytes(0), '0 B');
    assert.equal(formatBytes(1536), '1.5 KB');
    assert.equal(formatBytes(5 * 1024 ** 3), '5 GB');
    assert.equal(relativeTime(1000, 1030), 'Just now');
    assert.equal(relativeTime(1000, 1000 + 120), '2 minutes ago');
    assert.equal(relativeTime(1000, 1000 + 3600), '1 hour ago');
    assert.equal(relativeTime(1000, 1000 + 90000), 'Yesterday');
    assert.equal(relativeTime(1000, 1000 + 86400 * 3), '3 days ago');
    assert.equal(relativeTime(0, 5), '');
});

test('a large library is searched and grouped in memory without a perceptible wait', () => {
    const store = createStore();
    const rows = [];
    for (let i = 0; i < 8000; i += 1) rows.push(row('r' + i, {title: 'Synthetic item ' + i, source: 'agent_' + (i % 12), updated: i, labels: ['tag' + (i % 7)]}));
    store.applyChanges(page(rows, 8000));
    const started = performance.now();
    const hits = store.query({text: 'item 77 tag3'});
    store.facets({text: 'item'});
    store.groups(store.query({}), 6);
    assert.ok(hits.length > 0);
    assert.ok(performance.now() - started < 1500, 'queries over 8,000 rows stay quick');
});

test('only addresses that stay on this site are ever followed', () => {
    for (const ok of ['/agent/notes_agent/', '/api/admin/content/items/abc/content?download=1', '../assets/x.png', './y', '#top', 'blob:http://127.0.0.1/abc']) {
        assert.equal(safeUrl(ok), ok, ok);
    }
    for (const bad of ['javascript:alert(1)', ' javascript:alert(1)', 'data:text/html,<b>x</b>', 'https://example.test/', 'http://example.test', '//example.test/x', '/\\example.test', 'vbscript:x', '', null, undefined]) {
        assert.equal(safeUrl(bad), '', String(bad));
    }
});
