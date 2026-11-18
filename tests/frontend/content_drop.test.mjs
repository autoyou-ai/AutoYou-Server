import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../../assets/content-drop.js', import.meta.url), 'utf8');
const {collectDroppedFiles, relativePath} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

function fileEntry(name) {
    const file = {name, size: 4, lastModified: 1};
    return {name, isFile: true, file(resolve) { queueMicrotask(() => resolve(file)); }};
}
function directory(name, children, batchSize = 100) {
    return {name, isDirectory: true, createReader() {
        let offset = 0;
        return {readEntries(resolve) {
            const batch = children.slice(offset, offset + batchSize);
            offset += batchSize;
            queueMicrotask(() => resolve(batch));
        }};
    }};
}
function transfer(...entries) {
    return {items: entries.map(entry => ({kind: 'file', webkitGetAsEntry: () => entry, getAsFile: () => null})), files: []};
}

test('a dropped music folder includes every directory batch and nested paths', async () => {
    const children = Array.from({length: 205}, (_, i) => fileEntry(`track-${i}.wav`));
    children.push(directory('Live', [fileEntry('recording.wav')]));
    const counts = [];
    const files = await collectDroppedFiles(transfer(directory('Music', children)), count => counts.push(count));
    assert.equal(files.length, 206);
    assert.equal(relativePath(files[0]), 'Music/track-0.wav');
    assert.equal(relativePath(files[205]), 'Music/Live/recording.wav');
    assert.deepEqual(counts, [1, 100, 200, 206]);
});

test('all drop entries are captured while drag data is readable', async () => {
    let readable = true;
    const data = transfer(directory('First', [fileEntry('same.wav')]), directory('Second', [fileEntry('same.wav')]));
    for (const item of data.items) {
        const getEntry = item.webkitGetAsEntry;
        item.webkitGetAsEntry = () => { assert.equal(readable, true); return getEntry(); };
        item.getAsFile = () => { assert.equal(readable, true); return null; };
    }
    const result = collectDroppedFiles(data);
    readable = false;
    const files = await result;
    assert.deepEqual(files.map(relativePath), ['First/same.wav', 'Second/same.wav']);
});

test('plain file drops and folder-picker paths remain usable without entry APIs', async () => {
    const file = Object.freeze({name: 'song.wav', webkitRelativePath: 'Album/song.wav'});
    const withoutItems = await collectDroppedFiles({files: [file]});
    const withoutEntries = await collectDroppedFiles({items: [{kind: 'string'}, {kind: 'file', getAsFile: () => file}]});
    assert.deepEqual(withoutItems, [file]);
    assert.deepEqual(withoutEntries, [file]);
    assert.equal(relativePath(file), 'Album/song.wav');
});

test('an unreadable nested file rejects the entire discovery, with its path', async () => {
    const denied = {name: 'locked.wav', isFile: true, file(resolve, reject) { reject(new Error('denied')); }};
    await assert.rejects(collectDroppedFiles(transfer(directory('Music', [fileEntry('good.wav'), denied]))),
        /Could not read Music\/locked.wav.*Nothing has been uploaded yet/);
});

test('an empty folder returns an empty selection and final count', async () => {
    const counts = [];
    assert.deepEqual(await collectDroppedFiles(transfer(directory('Empty', [])), count => counts.push(count)), []);
    assert.deepEqual(counts, [0]);
});

test('oversized and excessively nested folder drops fail before returning a selection', async () => {
    const files = Array.from({length: 10001}, (_, i) => fileEntry(`file-${i}.txt`));
    await assert.rejects(collectDroppedFiles(transfer(directory('Huge', files))), /10,000 files.*Nothing has been uploaded yet/);
    let deep = fileEntry('leaf.txt');
    for (let i = 0; i < 66; i++) deep = directory(`Level${i}`, [deep]);
    await assert.rejects(collectDroppedFiles(transfer(deep)), /deeply nested.*Nothing has been uploaded yet/);
});
