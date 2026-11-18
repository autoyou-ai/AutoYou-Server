// Shared by the content library, TV and Audio. No file bytes are read here.
// Directory readers return batches, so keep reading until an empty batch.
// https://developer.mozilla.org/en-US/docs/Web/API/FileSystemDirectoryReader/readEntries
const paths = new WeakMap();

export function relativePath(file) {
    return paths.get(file) || file.webkitRelativePath || file.name;
}

export async function collectDroppedFiles(transfer, onProgress = () => {}) {
    // Capture every entry before awaiting: drag data is only readable inside drop.
    const items = Array.from(transfer.items || []).filter(item => item.kind === 'file');
    const entries = items.map(item => {
        const getEntry = item.getAsEntry || item.webkitGetAsEntry;
        return {entry: getEntry ? getEntry.call(item) : null, file: item.getAsFile()};
    });
    const fallback = Array.from(transfer.files || []);
    const files = [];
    let visited = 0;
    function add(file, path) {
        if (!file) return;
        if (files.length >= 10000) throw new Error('Choose fewer than 10,000 files at a time. Nothing has been uploaded yet.');
        paths.set(file, path || file.webkitRelativePath || file.name);
        files.push(file);
        if (files.length === 1 || files.length % 100 === 0) onProgress(files.length);
    }
    async function walk(entry, parent, depth) {
        if (depth > 64 || ++visited > 50000) throw new Error('This folder is too large or deeply nested. Choose a smaller folder. Nothing has been uploaded yet.');
        const path = parent + entry.name;
        try {
            if (entry.isFile) {
                add(await new Promise((resolve, reject) => entry.file(resolve, reject)), path);
            } else if (entry.isDirectory) {
                const reader = entry.createReader();
                while (true) {
                    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
                    if (!batch.length) break;
                    for (const child of batch) await walk(child, path + '/', depth + 1);
                }
            }
        } catch (error) {
            if (error instanceof Error && error.message.includes('Nothing has been uploaded yet.')) throw error;
            throw new Error('Could not read ' + path + '. Choose the folder again. Nothing has been uploaded yet.');
        }
    }
    if (entries.length) {
        for (const value of entries) {
            if (value.entry) await walk(value.entry, '', 0);
            else add(value.file);
        }
    } else {
        for (const file of fallback) add(file);
    }
    onProgress(files.length);
    return files;
}

// The Audio player still uses classic scripts; TV and the library use imports.
globalThis.AutoYouFileDrop = {collectDroppedFiles, relativePath};
