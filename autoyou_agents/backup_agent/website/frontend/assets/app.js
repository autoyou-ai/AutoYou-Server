/* The proxy buffers one HTTP request, so every transfer request stays bounded. */
const byId = (id) => document.getElementById(id);
const filesInput = byId("files-input");
const folderInput = byId("folder-input");
const startButton = byId("start");
const pauseButton = byId("pause");
const progress = byId("progress");
const statusLine = byId("status");
let paused = false;
let running = false;
let activeRequest = null;
let selectedFiles = [];
let nextFilesOffset = null;

function bytes(value) {
  if (value < 1024) return `${value} B`;
  const unit = ["KB", "MB", "GB", "TB"];
  let amount = value / 1024, i = 0;
  while (amount >= 1024 && i < unit.length - 1) { amount /= 1024; i++; }
  return `${amount.toFixed(amount >= 10 ? 0 : 1)} ${unit[i]}`;
}

function relativePath(file) {
  return file.webkitRelativePath || file.name;
}

function cleanName(value) {
  return value.replace(/[^A-Za-z0-9._ -]+/g, "_").replace(/^[ .]+|[ .]+$/g, "").slice(0, 180);
}

function cleanPath(file) {
  return relativePath(file).split("/").map(cleanName).join("/");
}

function resumeKey(file) {
  return `autoyou-backup:${relativePath(file)}:${file.size}:${file.lastModified}`;
}

function resumeRead(key) {
  try { return JSON.parse(localStorage.getItem(key) || "null"); } catch { return null; }
}

function resumeWrite(key, value) {
  try { if (value) localStorage.setItem(key, JSON.stringify(value)); else localStorage.removeItem(key); }
  catch { statusLine.textContent = "Browser storage is unavailable. Keep this page open until the transfer completes."; }
}

async function api(path, options = {}) {
  const controller = new AbortController();
  activeRequest = controller;
  try {
    const response = await fetch(new URL(path, document.baseURI), {
      credentials: "same-origin", cache: "no-store", ...options, signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({ error: response.statusText }));
    if (!response.ok || payload.success === false) {
      const error = new Error(payload.error || `Request failed (${response.status})`);
      error.status = response.status;
      error.offset = payload.offset;
      throw error;
    }
    return payload;
  } finally {
    if (activeRequest === controller) activeRequest = null;
  }
}

async function hash(chunk) {
  const digest = await crypto.subtle.digest("SHA-256", await chunk.arrayBuffer());
  return Array.from(new Uint8Array(digest), (n) => n.toString(16).padStart(2, "0")).join("");
}

async function uploadFile(file, index, completedBytes, totalBytes) {
  const key = resumeKey(file);
  const saved = resumeRead(key);
  let transfer;
  if (saved && saved.id && saved.token) {
    try {
      transfer = await api(`api/uploads/${saved.id}`, { headers: { "X-Transfer-Token": saved.token } });
      transfer.token = saved.token;
      if (transfer.name !== cleanName(file.name) || transfer.path !== cleanPath(file) || transfer.size !== file.size) {
        throw new Error("The saved transfer does not match this file.");
      }
    } catch (error) {
      if (error.status !== 404) throw error;
      resumeWrite(key, null);
    }
  }
  if (!transfer) {
    transfer = await api("api/uploads", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: file.name, path: relativePath(file), size: file.size }),
    });
    resumeWrite(key, { id: transfer.id, token: transfer.token });
  }
  let offset = transfer.offset;
  const chunkSize = transfer.chunk_size;
  while (offset < file.size && !paused) {
    const chunk = file.slice(offset, Math.min(offset + chunkSize, file.size));
    const checksum = await hash(chunk);
    try {
      const result = await api(`api/uploads/${transfer.id}/chunk`, {
        method: "PUT", body: chunk,
        headers: { "X-Transfer-Token": transfer.token, "X-Chunk-Offset": String(offset),
                   "X-Chunk-SHA256": checksum, "Content-Type": "application/octet-stream" },
      });
      offset = result.offset;
    } catch (error) {
      if (error.status !== 409) throw error;
      const current = await api(`api/uploads/${transfer.id}`, { headers: { "X-Transfer-Token": transfer.token } });
      offset = current.offset;
    }
    progress.value = totalBytes ? Math.floor((completedBytes + offset) * 100 / totalBytes) :
      Math.floor(index * 100 / selectedFiles.length);
    statusLine.textContent = `${index + 1} of ${selectedFiles.length}: ${relativePath(file)} - ${bytes(offset)} of ${bytes(file.size)}`;
  }
  if (paused) return false;
  const result = await api(`api/uploads/${transfer.id}/complete`, {
    method: "POST", headers: { "X-Transfer-Token": transfer.token },
  });
  resumeWrite(key, null);
  statusLine.textContent = `Saved ${result.path}. SHA-256: ${result.sha256}`;
  return true;
}

async function uploadSelection() {
  if (!selectedFiles.length || running) return;
  running = true; paused = false; startButton.disabled = true; pauseButton.disabled = false;
  try {
    const descriptor = selectedFiles.map((file) => [relativePath(file), file.size, file.lastModified]);
    const batchKey = `autoyou-backup-batch:${await hash(new Blob([JSON.stringify(descriptor)]))}`;
    const totalBytes = selectedFiles.reduce((sum, file) => sum + file.size, 0);
    const batch = resumeRead(batchKey);
    let completedCount = Number.isInteger(batch?.completedCount) && batch.completedCount >= 0 &&
      batch.completedCount <= selectedFiles.length ? batch.completedCount : 0;
    let completedBytes = selectedFiles.slice(0, completedCount).reduce((sum, file) => sum + file.size, 0);
    for (let index = completedCount; index < selectedFiles.length; index++) {
      if (paused) break;
      const saved = await uploadFile(selectedFiles[index], index, completedBytes, totalBytes);
      if (!saved) break;
      completedBytes += selectedFiles[index].size;
      completedCount = index + 1;
      resumeWrite(batchKey, { completedCount });
    }
    if (paused) {
      statusLine.textContent = "Paused. Choose the same files or folder to resume.";
    } else {
      resumeWrite(batchKey, null);
      progress.value = 100;
      statusLine.textContent = `Saved ${selectedFiles.length} ${selectedFiles.length === 1 ? "file" : "files"}.`;
      await refreshFiles();
    }
  } catch (error) {
    statusLine.textContent = paused ? "Paused. Choose the same files or folder to resume." :
      `${error.message} Choose the same files or folder to resume.`;
  } finally {
    running = false; activeRequest = null; startButton.disabled = !selectedFiles.length; pauseButton.disabled = true;
  }
}

async function download(item) {
  try {
    if (typeof window.showSaveFilePicker !== "function" && item.size > 64 * 1024 * 1024) {
      throw new Error("Large downloads need a browser with Save File support. The backup remains on this computer.");
    }
    const writable = typeof window.showSaveFilePicker === "function" ?
      await (await window.showSaveFilePicker({ suggestedName: item.name })).createWritable() : null;
    const blocks = [];
    let offset = 0;
    while (offset < item.size) {
      const response = await fetch(new URL(`api/files/${item.id}/chunk?offset=${offset}`, document.baseURI),
        { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error(`Download stopped (${response.status})`);
      const block = await response.arrayBuffer();
      const next = Number(response.headers.get("X-Next-Offset"));
      if (!block.byteLength || next !== offset + block.byteLength) throw new Error("Download chunk was incomplete.");
      if (writable) await writable.write(block); else blocks.push(block);
      offset = next;
      statusLine.textContent = `Downloaded ${bytes(offset)} of ${bytes(item.size)}`;
    }
    if (writable) await writable.close();
    else {
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob(blocks));
      link.download = item.name;
      link.click();
      setTimeout(() => URL.revokeObjectURL(link.href), 60000);
    }
    statusLine.textContent = `Downloaded ${item.name}`;
  } catch (error) { statusLine.textContent = error.message; }
}

async function refreshFiles(reset = true) {
  const list = byId("files");
  try {
    if (reset) { list.replaceChildren(); nextFilesOffset = 0; }
    const result = await api(`api/files?offset=${nextFilesOffset}`);
    nextFilesOffset = result.next_offset;
    for (const item of result.items) {
      const row = document.createElement("div"); row.className = "file-row";
      const info = document.createElement("div");
      const name = document.createElement("strong"); name.textContent = item.path || item.name;
      const size = document.createElement("small"); size.textContent = `${bytes(item.size)} - SHA-256 ${item.sha256}`;
      info.append(name, size);
      const button = document.createElement("button"); button.textContent = "Download";
      button.addEventListener("click", () => download(item));
      row.append(info, button); list.append(row);
    }
    if (!list.children.length && nextFilesOffset === null) list.textContent = "No backups yet.";
    byId("more").hidden = nextFilesOffset === null;
  } catch (error) { list.textContent = error.message; }
}

function selectFiles(input, other) {
  if (running) return;
  other.value = "";
  selectedFiles = Array.from(input.files || []).sort((a, b) =>
    relativePath(a).localeCompare(relativePath(b)) || a.size - b.size || a.lastModified - b.lastModified);
  const total = selectedFiles.reduce((sum, file) => sum + file.size, 0);
  byId("selected").textContent = selectedFiles.length ?
    `${selectedFiles.length} ${selectedFiles.length === 1 ? "file" : "files"} selected (${bytes(total)})` :
    "No files selected";
  startButton.disabled = !selectedFiles.length;
  progress.value = 0;
}

filesInput.addEventListener("change", () => selectFiles(filesInput, folderInput));
folderInput.addEventListener("change", () => selectFiles(folderInput, filesInput));
startButton.addEventListener("click", uploadSelection);
pauseButton.addEventListener("click", () => { paused = true; activeRequest?.abort(); });
byId("refresh").addEventListener("click", refreshFiles);
byId("more").addEventListener("click", () => refreshFiles(false));
refreshFiles();
