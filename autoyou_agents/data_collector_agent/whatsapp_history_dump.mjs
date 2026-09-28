// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-C-746f20706179203130252061-faf0d60d91e3410ea18a7cc3

// Data Collector owns WhatsApp history collection; Fine Tuning only consumes exports.
import fs from 'fs';
import path from 'path';
import { createRequire } from 'module';
import { fileURLToPath, pathToFileURL } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const repoRoot = path.resolve(__dirname, '..', '..');

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (!value.startsWith('--')) {
      continue;
    }
    const key = value.slice(2);
    const next = argv[index + 1];
    if (!next || next.startsWith('--')) {
      options[key] = true;
      continue;
    }
    options[key] = next;
    index += 1;
  }
  return options;
}

function asInt(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ''), 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(minimum, Math.min(maximum, parsed));
}

function safeText(value) {
  return String(value ?? '').replace(/\r\n?/g, '\n').trim();
}

function serializedId(value) {
  if (!value) {
    return '';
  }
  if (typeof value === 'string') {
    return value;
  }
  if (value._serialized) {
    return value._serialized;
  }
  if (value.user && value.server) {
    return `${value.user}@${value.server}`;
  }
  return String(value);
}

function resolveCachedWebVersion(cacheDir) {
  try {
    if (!fs.existsSync(cacheDir)) {
      return undefined;
    }
    const files = fs
      .readdirSync(cacheDir)
      .filter((fileName) => fileName.endsWith('.html'))
      .map((fileName) => ({
        fileName,
        mtime: fs.statSync(path.join(cacheDir, fileName)).mtimeMs,
      }))
      .sort((a, b) => b.mtime - a.mtime);
    if (!files.length) {
      return undefined;
    }
    return files[0].fileName.replace(/\.html$/, '');
  } catch (error) {
    return undefined;
  }
}

function chatMatchesScope(chat, scope) {
  const isGroup = Boolean(chat?.isGroup) || serializedId(chat?.id).endsWith('@g.us');
  if (scope === 'groups') {
    return isGroup;
  }
  if (scope === 'personal') {
    return !isGroup;
  }
  return true;
}

function messageTimestamp(message) {
  const raw = Number(message?.timestamp || message?._data?.t || 0);
  return Number.isFinite(raw) ? raw : 0;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchMessagesFromStore(client, chatId, deadlineMs) {
  const result = await client.pupPage.evaluate(async ({ chatId: targetChatId, deadlineMs: targetDeadlineMs }) => {
    const timedOut = () => Date.now() >= targetDeadlineMs;
    const serializeId = (value) => {
      if (!value) {
        return '';
      }
      if (typeof value === 'string') {
        return value;
      }
      if (value._serialized) {
        return value._serialized;
      }
      if (value.user && value.server) {
        return `${value.user}@${value.server}`;
      }
      return String(value);
    };
    const messageBelongsToChat = (message) => {
      const remote = serializeId(message?.id?.remote || message?.remote || message?.chatId);
      if (remote === targetChatId) {
        return true;
      }
      const from = serializeId(message?.from);
      const to = serializeId(message?.to);
      return from === targetChatId || to === targetChatId;
    };
    const knownChats = window.Store.Chat.getModelsArray ? window.Store.Chat.getModelsArray() : [];
    const chat = knownChats.find((candidate) => candidate?.id?._serialized === targetChatId);
    if (!chat) {
      return { messages: [], error: 'Chat was not found in WhatsApp Store.' };
    }
    const msgFilter = (message) => !message.isNotification && messageBelongsToChat(message);
    const byId = new Map();
    const addMessages = (items) => {
      for (const message of items || []) {
        if (!msgFilter(message)) {
          continue;
        }
        const id = serializeId(message?.id) || `${serializeId(message?.from)}:${message?.t || message?.timestamp || byId.size}`;
        if (!byId.has(id)) {
          byId.set(id, message);
        }
      }
    };
    addMessages(chat.msgs?.getModelsArray ? chat.msgs.getModelsArray() : []);
    addMessages(window.Store.Msg?.getModelsArray ? window.Store.Msg.getModelsArray() : []);
    addMessages(Array.isArray(window.Store.Msg?._models) ? window.Store.Msg._models : []);
    let loadError = '';
    let messages = Array.from(byId.values());
    while (!timedOut()) {
      const previousSize = byId.size;
      let loadedMessages = [];
      try {
        loadedMessages = await window.Store.ConversationMsgs.loadEarlierMsgs(chat, chat.msgs);
      } catch (error) {
        loadError = error?.message || String(error);
        break;
      }
      if (!loadedMessages || !loadedMessages.length) {
        break;
      }
      addMessages(loadedMessages);
      messages = Array.from(byId.values());
      if (byId.size <= previousSize) {
        break;
      }
    }
    messages.sort((left, right) => ((left.t || 0) > (right.t || 0) ? 1 : -1));
    return {
      messages: messages.map((message) => {
        try {
          return window.WWebJS.getMessageModel(message);
        } catch (_) {
          const serialized = typeof message.serialize === 'function' ? message.serialize() : {};
          return {
            ...serialized,
            id: serialized.id || message.id,
            from: serialized.from || serializeId(message.from),
            to: serialized.to || serializeId(message.to),
            author: serialized.author || serializeId(message.author),
            fromMe: Boolean(message.id?.fromMe || message.fromMe),
            timestamp: message.t || message.timestamp,
            type: message.type,
            body: message.body || message.caption || serialized.body || serialized.caption || '',
          };
        }
      }),
      error: loadError,
      timeLimitReached: timedOut(),
    };
  }, { chatId, deadlineMs });
  return result || { messages: [], error: 'No Store result returned.' };
}

async function fetchMessagesFromIndexedDb(client, chatId, deadlineMs) {
  const result = await client.pupPage.evaluate(async ({ chatId: targetChatId, deadlineMs: targetDeadlineMs }) => {
    const timedOut = () => Date.now() >= targetDeadlineMs;
    const serializeId = (value) => {
      if (!value) {
        return '';
      }
      if (typeof value === 'string') {
        return value;
      }
      if (value._serialized) {
        return value._serialized;
      }
      if (value.user && value.server) {
        return `${value.user}@${value.server}`;
      }
      if (value.server && value.user) {
        return `${value.user}@${value.server}`;
      }
      return String(value);
    };
    const textFromRecord = (record) => String(
      record?.body ||
      record?.caption ||
      record?.text ||
      record?.message ||
      record?.quotedMsgBody ||
      record?.pollName ||
      '',
    ).trim();
    const timestampFromRecord = (record) => {
      const raw = Number(record?.t || record?.timestamp || record?.messageTimestamp || record?.sortTimestamp || 0);
      return Number.isFinite(raw) ? raw : 0;
    };
    const recordChatIds = (record) => {
      const ids = new Set();
      for (const value of [
        record?.chatId,
        record?.chat,
        record?.remote,
        record?.remoteChatId,
        record?.id?.remote,
        record?.id?.remote?._serialized,
        record?.id?.remote?.toString ? record.id.remote.toString() : '',
        record?.from,
        record?.to,
      ]) {
        const id = serializeId(value);
        if (id) {
          ids.add(id);
        }
      }
      return ids;
    };
    const recordMatchesChat = (record) => recordChatIds(record).has(targetChatId);
    const normalizeRecord = (record) => {
      const serialized = typeof record?.serialize === 'function' ? record.serialize() : {};
      const id = serialized.id || record?.id || {};
      return {
        id,
        from: serialized.from || serializeId(record?.from),
        to: serialized.to || serializeId(record?.to),
        author: serialized.author || serializeId(record?.author || record?.sender),
        fromMe: Boolean(serialized.fromMe || record?.fromMe || record?.id?.fromMe),
        timestamp: serialized.timestamp || timestampFromRecord(record),
        type: serialized.type || record?.type || record?.subtype || 'chat',
        body: serialized.body || serialized.caption || textFromRecord(record),
        _source: 'indexeddb-message-store',
      };
    };
    const openDatabase = (name) => new Promise((resolve, reject) => {
      const request = indexedDB.open(name);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error(`Could not open ${name}`));
    });
    const hydrateMessageIds = async (messageIds) => {
      const byId = new Map();
      for (let index = 0; index < messageIds.length && !timedOut(); index += 100) {
        const chunk = messageIds.slice(index, index + 100);
        try {
          const loaded = await window.Store.Msg.getMessagesById(chunk);
          for (const message of loaded?.messages || []) {
            if (!recordMatchesChat(message) || !textFromRecord(message)) {
              continue;
            }
            const id = serializeId(message?.id) || serializeId(message?.id?._serialized) || String(byId.size);
            if (!byId.has(id)) {
              try {
                byId.set(id, window.WWebJS.getMessageModel(message));
              } catch (_) {
                byId.set(id, normalizeRecord(message));
              }
            }
          }
        } catch (_) {
          // Some IDs may no longer hydrate; continue with the rest.
        }
      }
      return { messages: Array.from(byId.values()), timeLimitReached: timedOut() };
    };
    const cursorRows = (store) => new Promise((resolve, reject) => {
      const ids = [];
      const rawRows = [];
      const request = store.openCursor();
      request.onsuccess = () => {
        if (timedOut()) {
          resolve({ ids, rawRows, timeLimitReached: true });
          return;
        }
        const cursor = request.result;
        if (!cursor) {
          resolve({ ids, rawRows, timeLimitReached: false });
          return;
        }
        const value = cursor.value;
        if (recordMatchesChat(value)) {
          const id = serializeId(value?.id || value?.internalId);
          if (id) {
            ids.push(id);
          }
          if (textFromRecord(value)) {
            rawRows.push(normalizeRecord(value));
          }
        }
        cursor.continue();
      };
      request.onerror = () => reject(request.error || new Error('Could not scan message store'));
    });
    try {
      const db = await openDatabase('model-storage');
      try {
        if (!Array.from(db.objectStoreNames || []).includes('message')) {
          return { messages: [], error: 'IndexedDB model-storage.message store was not found.' };
        }
        const tx = db.transaction('message', 'readonly');
        const store = tx.objectStore('message');
        const { ids, rawRows, timeLimitReached: cursorTimedOut } = await cursorRows(store);
        const hydrated = await hydrateMessageIds(ids);
        let rows = hydrated.messages;
        if (!rows.length && rawRows.length) {
          rows = rawRows;
        }
        rows.sort((left, right) => (Number(left.timestamp || 0) > Number(right.timestamp || 0) ? 1 : -1));
        return {
          messages: rows,
          indexedDbIdCount: ids.length,
          scannedCount: rows.length,
          error: '',
          timeLimitReached: cursorTimedOut || hydrated.timeLimitReached || timedOut(),
        };
      } finally {
        db.close();
      }
    } catch (error) {
      return { messages: [], error: error?.message || String(error) };
    }
  }, { chatId, deadlineMs });
  return result || { messages: [], scannedCount: 0, error: 'No IndexedDB result returned.' };
}

async function collectStoreDiagnostics(client) {
  try {
    return await client.pupPage.evaluate(async () => {
      const safeKeys = (value) => {
        try {
          return Object.keys(value || {}).filter((key) => !key.startsWith('_')).sort().slice(0, 80);
        } catch (_) {
          return [];
        }
      };
      const indexedDbDatabases = [];
      const messageStoreSamples = [];
      const messageStoreIndexNames = [];
      const messageStoreFieldNames = new Set();
      try {
        if (indexedDB.databases) {
          const databases = await indexedDB.databases();
          for (const database of databases || []) {
            const name = database?.name || '';
            if (!name) {
              continue;
            }
            const entry = { name, version: database?.version || null, stores: [] };
            try {
              const opened = await new Promise((resolve, reject) => {
                const request = indexedDB.open(name);
                request.onsuccess = () => resolve(request.result);
                request.onerror = () => reject(request.error || new Error(`Could not open ${name}`));
              });
              const storeNames = Array.from(opened.objectStoreNames || []);
              for (const storeName of storeNames.slice(0, 80)) {
                const storeEntry = { name: storeName, count: null, indexes: [] };
                try {
                  const tx = opened.transaction(storeName, 'readonly');
                  const store = tx.objectStore(storeName);
                  storeEntry.indexes = Array.from(store.indexNames || []).slice(0, 20);
                  if (name === 'model-storage' && storeName === 'message') {
                    messageStoreIndexNames.push(...Array.from(store.indexNames || []));
                    await new Promise((resolve) => {
                      let inspected = 0;
                      const cursorRequest = store.openCursor();
                      cursorRequest.onsuccess = () => {
                        const cursor = cursorRequest.result;
                        if (!cursor || inspected >= 500) {
                          resolve();
                          return;
                        }
                        inspected += 1;
                        const value = cursor.value || {};
                        for (const key of Object.keys(value)) {
                          messageStoreFieldNames.add(key);
                        }
                        if (messageStoreSamples.length < 5) {
                          const sample = {};
                          for (const key of Object.keys(value).sort()) {
                            if (/body|caption|text|message|name|phone|jid|id|from|to|author|sender/i.test(key)) {
                              const item = value[key];
                              sample[key] = {
                                type: item === null ? 'null' : Array.isArray(item) ? 'array' : typeof item,
                                keys: item && typeof item === 'object' ? Object.keys(item).sort().slice(0, 12) : [],
                                hasSerialized: Boolean(item && typeof item === 'object' && item._serialized),
                                stringLike: typeof item === 'string',
                              };
                            } else if (/chat|remote|timestamp|time|type|fromMe/i.test(key)) {
                              const item = value[key];
                              sample[key] = {
                                type: item === null ? 'null' : Array.isArray(item) ? 'array' : typeof item,
                                keys: item && typeof item === 'object' ? Object.keys(item).sort().slice(0, 12) : [],
                                hasSerialized: Boolean(item && typeof item === 'object' && item._serialized),
                                stringLike: typeof item === 'string',
                              };
                            }
                          }
                          messageStoreSamples.push(sample);
                        }
                        cursor.continue();
                      };
                      cursorRequest.onerror = () => resolve();
                    });
                  }
                  storeEntry.count = await new Promise((resolve) => {
                    const countRequest = store.count();
                    countRequest.onsuccess = () => resolve(countRequest.result);
                    countRequest.onerror = () => resolve(null);
                  });
                } catch (_) {
                  // Keep the store name even when a count fails.
                }
                entry.stores.push(storeEntry);
              }
              opened.close();
            } catch (error) {
              entry.error = error?.message || String(error);
            }
            indexedDbDatabases.push(entry);
          }
        }
      } catch (error) {
        indexedDbDatabases.push({ error: error?.message || String(error) });
      }
      return {
        storeKeys: safeKeys(window.Store),
        msgKeys: safeKeys(window.Store?.Msg),
        chatKeys: safeKeys(window.Store?.Chat),
        conversationMsgsKeys: safeKeys(window.Store?.ConversationMsgs),
        historySyncKeys: safeKeys(window.Store?.HistorySync),
        hasWWebJSMessageModel: Boolean(window.WWebJS?.getMessageModel),
        messageStoreIndexNames: Array.from(new Set(messageStoreIndexNames)).sort(),
        messageStoreFieldNames: Array.from(messageStoreFieldNames).sort(),
        messageStoreSamples,
        indexedDbDatabases,
      };
    });
  } catch (error) {
    return { error: safeText(error?.message || error) };
  }
}

async function withTimeout(promise, timeoutMs, label) {
  let timer = null;
  const timeoutPromise = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs} ms`)), timeoutMs);
  });
  try {
    return await Promise.race([promise, timeoutPromise]);
  } finally {
    if (timer) {
      clearTimeout(timer);
    }
  }
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const outputPath = path.resolve(String(options.output || ''));
  if (!outputPath) {
    throw new Error('--output is required');
  }

  const nodeWhatsappDir = path.resolve(
    String(options['node-whatsapp-dir'] || process.env.AUTOYOU_WHATSAPP_NODE_DIR || path.join(repoRoot, 'node', 'whatsapp')),
  );
  const authPath = path.resolve(String(options['auth-path'] || process.env.WWEBJS_AUTH_PATH || path.join(repoRoot, 'whatsapp', '.wwebjs_auth')));
  const cachePath = path.resolve(String(options['cache-path'] || process.env.WWEBJS_CACHE_PATH || path.join(repoRoot, 'whatsapp', '.wwebjs_cache')));
  const clientId = safeText(options['client-id'] || process.env.DEVICE_NAME || 'AutoYou-WhatsApp');
  const scope = ['personal', 'groups', 'all'].includes(String(options.scope || '').toLowerCase())
    ? String(options.scope).toLowerCase()
    : 'personal';
  const allAvailableHistory = true;
  const historyTimeoutMs = asInt(options['history-timeout-ms'], 14400000, 60000, 86400000);
  const readyTimeoutMs = asInt(options['ready-timeout-ms'], 120000, 10000, 600000);
  // Timeline window (inclusive). YYYY-MM-DD -> epoch seconds; 0 means unbounded.
  const dateBoundaryEpoch = (value, endOfDay) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || '').trim());
    if (!m) return 0;
    const d = new Date(
      Number(m[1]), Number(m[2]) - 1, Number(m[3]),
      endOfDay ? 23 : 0, endOfDay ? 59 : 0, endOfDay ? 59 : 0, endOfDay ? 999 : 0,
    );
    const epoch = Math.floor(d.getTime() / 1000);
    return Number.isFinite(epoch) ? epoch : 0;
  };
  const earliestTs = dateBoundaryEpoch(options.earliest, false);
  const latestTs = dateBoundaryEpoch(options.latest, true);
  const withinTimeline = (ts) => {
    const value = Number(ts || 0);
    if (earliestTs && value && value < earliestTs) return false;
    if (latestTs && value && value > latestTs) return false;
    return true;
  };
  const syncHistory = String(options['sync-history'] ?? 'true').toLowerCase() !== 'false';
  const historySyncWaitMs = asInt(options['history-sync-wait-ms'], 3500, 0, 30000);
  const includeDiagnostics = ['1', 'true', 'yes', 'on'].includes(
    String(options.diagnostics ?? process.env.AUTOYOU_FINE_TUNING_DUMP_DIAGNOSTICS ?? '').toLowerCase().trim(),
  );

  if (!fs.existsSync(nodeWhatsappDir)) {
    throw new Error(`WhatsApp node directory was not found: ${nodeWhatsappDir}`);
  }
  if (!fs.existsSync(authPath)) {
    throw new Error(`WhatsApp auth directory was not found: ${authPath}`);
  }

  const requireFromWhatsapp = createRequire(path.join(nodeWhatsappDir, 'package.json'));
  const pkg = requireFromWhatsapp('whatsapp-web.js');
  const { Client: ChromiumClient, LocalAuth } = pkg;
  const webkit = process.platform === 'darwin' ? process.env.AUTOYOU_WEBKIT_EXECUTABLE : undefined;
  const { webKitClient } = await import(pathToFileURL(path.join(nodeWhatsappDir, 'native_webkit.js')).href);
  const Client = webkit ? webKitClient(ChromiumClient, webkit) : ChromiumClient;
  const chromiumModule = await import(pathToFileURL(path.join(nodeWhatsappDir, 'chromium_path.js')).href);
  const chromiumPath = chromiumModule.resolveChromiumExecutable(process.env.PUPPETEER_EXECUTABLE_PATH || '');
  const cachedWebVersion = resolveCachedWebVersion(cachePath);

  const client = new Client({
    authStrategy: new LocalAuth({ clientId, dataPath: authPath }),
    takeoverOnConflict: false,
    takeoverTimeoutMs: 0,
    deviceName: clientId,
    browserName: webkit ? 'Safari' : 'Chrome',
    ...(webkit ? { userAgent: false, webVersionCache: { type: 'none' } }
      : { webVersionCache: { type: 'local', path: cachePath }, ...(cachedWebVersion ? { webVersion: cachedWebVersion } : {}) }),
    puppeteer: {
      ...(chromiumPath ? { executablePath: chromiumPath } : {}),
      protocolTimeout: 120000,
      timeout: 120000,
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-accelerated-2d-canvas',
        '--no-first-run',
        '--no-zygote',
        '--disable-gpu',
      ],
    },
  });

  let qrSeen = false;
  const readyPromise = new Promise((resolve, reject) => {
    client.once('ready', resolve);
    client.once('qr', () => {
      qrSeen = true;
      reject(new Error('WhatsApp authentication is required before history can be dumped.'));
    });
    client.once('auth_failure', (message) => {
      reject(new Error(`WhatsApp authentication failed: ${safeText(message) || 'unknown error'}`));
    });
    client.once('disconnected', (reason) => {
      reject(new Error(`WhatsApp disconnected before dump completed: ${safeText(reason) || 'unknown reason'}`));
    });
  });

  try {
    await client.initialize();
    await withTimeout(readyPromise, readyTimeoutMs, 'WhatsApp history dump startup');
    const historyDeadlineMs = Date.now() + historyTimeoutMs;
    const remainingHistoryMs = () => Math.max(0, historyDeadlineMs - Date.now());

    const chats = (await client.getChats())
      .filter((chat) => chatMatchesScope(chat, scope))
      .sort((left, right) => Number(right?.timestamp || 0) - Number(left?.timestamp || 0));

    const exportedChats = [];
    // ponytail: keeps the selected run in one JSON payload; stream JSONL when full-history imports exceed available RAM.
    const exportedMessages = [];
    let timeLimitReached = false;
    const diagnostics = includeDiagnostics ? await collectStoreDiagnostics(client) : undefined;

    for (const chat of chats) {
      if (!remainingHistoryMs()) {
        timeLimitReached = true;
        break;
      }
      const chatId = serializedId(chat?.id);
      const isGroup = Boolean(chat?.isGroup) || chatId.endsWith('@g.us');
      const chatName = safeText(chat?.name || chat?.formattedTitle || chatId || 'WhatsApp chat');
      let messages = [];
      let syncResult = false;
      let syncError = '';
      if (syncHistory && typeof chat.syncHistory === 'function') {
        try {
          syncResult = Boolean(await withTimeout(chat.syncHistory(), remainingHistoryMs(), 'WhatsApp history sync'));
          if (historySyncWaitMs > 0) {
            await sleep(Math.min(historySyncWaitMs, remainingHistoryMs()));
          }
        } catch (error) {
          syncError = safeText(error?.message || error);
        }
        if (!remainingHistoryMs()) {
          timeLimitReached = true;
          break;
        }
      }
      try {
        messages = await withTimeout(chat.fetchMessages({ limit: Infinity }), remainingHistoryMs(), 'WhatsApp chat history');
      } catch (error) {
        const primaryError = safeText(error?.message || error);
        if (!remainingHistoryMs()) {
          timeLimitReached = true;
          exportedChats.push({
            id: chatId,
            name: chatName,
            isGroup,
            messageCount: 0,
            syncResult,
            syncError,
            error: primaryError,
          });
          break;
        }
        try {
          const fallback = await withTimeout(
            fetchMessagesFromStore(client, chatId, historyDeadlineMs),
            remainingHistoryMs(),
            'WhatsApp Store history fallback',
          );
          messages = fallback.messages || [];
          timeLimitReached ||= Boolean(fallback.timeLimitReached);
          if (!timeLimitReached) {
            const idbFallback = await withTimeout(
              fetchMessagesFromIndexedDb(client, chatId, historyDeadlineMs),
              remainingHistoryMs(),
              'WhatsApp IndexedDB history fallback',
            );
            if ((idbFallback.messages || []).length > messages.length) {
              messages = idbFallback.messages || [];
            }
            timeLimitReached ||= Boolean(idbFallback.timeLimitReached);
            if (!messages.length && idbFallback.error) {
              exportedChats.push({
                id: chatId,
                name: chatName,
                isGroup,
                messageCount: 0,
                syncResult,
                syncError,
                error: `${primaryError}; fallback: ${safeText(fallback.error)}; indexeddb: ${safeText(idbFallback.error)}`,
              });
              continue;
            }
          }
          if (!messages.length && fallback.error) {
            exportedChats.push({
              id: chatId,
              name: chatName,
              isGroup,
              messageCount: 0,
              syncResult,
              syncError,
              error: `${primaryError}; fallback: ${safeText(fallback.error)}`,
            });
            continue;
          }
        } catch (fallbackError) {
          exportedChats.push({
            id: chatId,
            name: chatName,
            isGroup,
            messageCount: 0,
            syncResult,
            syncError,
            error: `${primaryError}; fallback: ${safeText(fallbackError?.message || fallbackError)}`,
          });
          continue;
        }
      }
      messages.sort((left, right) => messageTimestamp(left) - messageTimestamp(right));
      let count = 0;
      for (const message of messages) {
        if (!remainingHistoryMs()) {
          timeLimitReached = true;
          break;
        }
        const body = safeText(message?.body || message?._data?.body || message?._data?.caption || '');
        if (!body) {
          continue;
        }
        const senderName = safeText(
          message?._data?.notifyName ||
            message?._data?.pushname ||
            message?._data?.sender?.pushname ||
            message?._data?.sender?.name ||
            '',
        );
        const msgTs = messageTimestamp(message);
        if (!withinTimeline(msgTs)) {
          continue;
        }
        exportedMessages.push({
          id: serializedId(message?.id),
          from: serializedId(message?.from),
          to: serializedId(message?.to),
          author: serializedId(message?.author),
          fromMe: Boolean(message?.fromMe || message?.id?.fromMe),
          timestamp: msgTs,
          type: safeText(message?.type || message?._data?.type || 'chat'),
          body,
          senderName,
          chat: {
            id: chatId,
            name: chatName,
            isGroup,
          },
        });
        count += 1;
      }
      exportedChats.push({
        id: chatId,
        name: chatName,
        isGroup,
        messageCount: count,
        syncResult,
        syncError,
      });
      if (timeLimitReached) {
        break;
      }
    }

    const payload = {
      generatedAt: new Date().toISOString(),
      source: 'whatsapp-web-js-history-dump',
      scope,
      allAvailableHistory,
      historyTimeBudgetSeconds: Math.floor(historyTimeoutMs / 1000),
      timeLimitReached,
      chatCount: exportedChats.length,
      messageCount: exportedMessages.length,
      chats: exportedChats,
      messages: exportedMessages,
    };
    if (diagnostics) {
      payload.diagnostics = diagnostics;
    }
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.writeFileSync(outputPath, JSON.stringify(payload, null, 2), 'utf8');
    console.log(JSON.stringify({
      ok: true,
      outputPath,
      chatCount: exportedChats.length,
      messageCount: exportedMessages.length,
      allAvailableHistory,
    }));
  } finally {
    try {
      await client.destroy();
    } catch (error) {
      if (!qrSeen) {
        console.warn(`Failed to destroy WhatsApp dump client: ${safeText(error?.message || error)}`);
      }
    }
  }
}

main().catch((error) => {
  console.error(JSON.stringify({
    ok: false,
    error: safeText(error?.message || error),
  }));
  process.exitCode = 1;
});
