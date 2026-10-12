// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-T-address-ce3d39d253720bbfe9cbfa1e

import pkg from 'whatsapp-web.js';
const { Client: ChromiumClient, LocalAuth, MessageMedia } = pkg;
import { webKitClient } from './native_webkit.js';
const WEBKIT_EXECUTABLE = process.platform === 'darwin' ? process.env.AUTOYOU_WEBKIT_EXECUTABLE : undefined;
const Client = WEBKIT_EXECUTABLE ? webKitClient(ChromiumClient, WEBKIT_EXECUTABLE) : ChromiumClient;
import { WebSocketServer } from 'ws';
import qrcode from 'qrcode-terminal';
import fetch from 'node-fetch';
import fs from 'fs';
import path from 'path';
import { spawn } from 'child_process';
import { fileURLToPath } from 'url';

import { resolveChromiumExecutable } from './chromium_path.js';
import {
    messageTextSummary,
    redactIdentifier,
} from './log_redaction.js';
import {
    classifyWhatsAppMessage,
    createOutboundMediaGuard,
    hasDeviceSignature,
    isIgnorableChannelMetadataLookupError,
    isIgnorableContactMetadataLookupError,
    messageFingerprint,
    normalizeWhatsAppAddress,
} from './message_filters.js';
import { installWWebJsRecoveryGuards } from './wwebjs_recovery.js';

// Derive __dirname for ESM to keep auth/cache paths stable across runs
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Stable session/auth and cache directories
const AUTH_DATA_PATH = process.env.WWEBJS_AUTH_PATH || path.resolve(path.join(__dirname, '.wwebjs_auth'));
const CACHE_PATH = process.env.WWEBJS_CACHE_PATH || path.resolve(path.join(__dirname, '.wwebjs_cache'));

// Configuration from environment variables
const WEBSOCKET_PORT = process.env.WEBSOCKET_PORT || 8083;
const DEVICE_NAME = process.env.DEVICE_NAME || 'AutoYou-WhatsApp';
let SERVER_NAME = process.env.SERVER_NAME || DEVICE_NAME;
const CHAT_API_URL = process.env.CHAT_API_URL || 'http://localhost:8081/api/chat';
// Offload threshold for media payloads over WebSocket (bytes). Default ~4 MiB.
const MAX_WS_MEDIA_BYTES = parseInt(process.env.MAX_WS_MEDIA_BYTES || '4194304', 10);
const PUPPETEER_PROTOCOL_TIMEOUT_MS = parseInt(process.env.WHATSAPP_PROTOCOL_TIMEOUT_MS || '120000', 10);
const PUPPETEER_LAUNCH_TIMEOUT_MS = parseInt(process.env.WHATSAPP_LAUNCH_TIMEOUT_MS || '120000', 10);
const SHUTDOWN_TIMEOUT_MS = parseInt(process.env.WHATSAPP_SHUTDOWN_TIMEOUT_MS || '15000', 10);
const AUTH_READY_TIMEOUT_MS = parseInt(process.env.WHATSAPP_AUTH_READY_TIMEOUT_MS || '90000', 10);
const MAX_AUTH_READY_RESTARTS = parseInt(process.env.WHATSAPP_AUTH_READY_RESTARTS || '2', 10);
const WHATSAPP_WEB_CACHE_MAX_AGE_MS = parseInt(
    process.env.WHATSAPP_WEB_CACHE_MAX_AGE_MS || '604800000',
    10,
);
const CHANNEL_METADATA_SKIP_LOG_INTERVAL_MS = parseInt(process.env.WHATSAPP_CHANNEL_METADATA_SKIP_LOG_INTERVAL_MS || '60000', 10);
// Explicit chromium path - set by AutoYou server to the bundled playwright/chromium
// when running as a packaged binary.  Puppeteer v24 checks PUPPETEER_EXECUTABLE_PATH
// automatically, but passing it explicitly via executablePath in the launch options
// is more reliable and avoids puppeteer falling back to its own download logic.
const RAW_CHROMIUM_EXECUTABLE_PATH = process.env.PUPPETEER_EXECUTABLE_PATH || '';

function parseBooleanSetting(rawValue, fallback) {
    const normalized = String(rawValue || '').trim().toLowerCase();
    if (['1', 'true', 'yes', 'on'].includes(normalized)) {
        return true;
    }
    if (['0', 'false', 'no', 'off'].includes(normalized)) {
        return false;
    }
    return fallback;
}

const WHATSAPP_BROWSER_HEADLESS = parseBooleanSetting(
    process.env.WHATSAPP_BROWSER_HEADLESS || process.env.AUTOYOU_BROWSER_HEADLESS,
    true,
);
const OBFUSCATED_DIAGNOSTIC_LOGGING = parseBooleanSetting(
    process.env.AUTOYOU_WHATSAPP_OBFUSCATED_LOGGING ||
        process.env.WHATSAPP_OBFUSCATED_LOGGING ||
        process.env.AUTOYOU_WHATSAPP_LOG_NON_SELF_SENT ||
        process.env.WHATSAPP_LOG_NON_SELF_SENT,
    false,
);

// Global state
let client = null;
let wss = null;
let wsConnections = new Set();
let currentQR = null;
let clientReady = false;
let phoneNumber = null;
let reconnectAttempts = 0;
let maxReconnectAttempts = 5;
let reconnectDelay = 5000; // 5 seconds
let isReconnecting = false;
let lastState = null;
let activeTypingChats = new Map(); // Store chat contexts when typing indicators are active
let sessionHealthCheckInterval = null;
let lastSuccessfulOperation = Date.now();
let sessionRecoveryAttempts = 0;
let maxSessionRecoveryAttempts = 3;
let isRestartingClient = false;
let isShuttingDown = false;
const recentInboundMessageIds = new Map();
const recentOutboundMessageIds = new Map();
const outboundMediaGuard = createOutboundMediaGuard();
const INBOUND_MESSAGE_DEDUP_TTL_MS = 5 * 60 * 1000;
let lastSelfChatId = null;
let clientAuthenticated = false;
let lastAuthenticatedAt = null;
let authReadyWatchdogTimer = null;
let authReadyRestartAttempts = 0;
let authReadyStalled = false;
let useFreshWebVersion = false;
let lastChannelMetadataSkipLogAt = 0;
const PARENT_PID = Number.parseInt(process.env.AUTOYOU_PARENT_PID || '0', 10);
let parentWatchdogTimer = null;

function forceTerminateOwnProcessTree() {
    if (process.platform === 'win32') {
        const killer = spawn('taskkill', ['/F', '/T', '/PID', String(process.pid)], {
            windowsHide: true,
            stdio: 'ignore',
        });
        killer.once('error', () => process.exit(0));
        killer.once('exit', () => process.exit(0));
        return;
    }
    try {
        process.kill(-process.pid, 'SIGKILL');
    } catch (_) {
        process.exit(0);
    }
}

function startParentWatchdog() {
    if (!Number.isInteger(PARENT_PID) || PARENT_PID <= 0 || PARENT_PID === process.pid) {
        return;
    }

    parentWatchdogTimer = setInterval(() => {
        let parentAlive = process.ppid === PARENT_PID;
        if (parentAlive && process.platform !== 'win32') {
            try {
                process.kill(PARENT_PID, 0);
            } catch (_) {
                parentAlive = false;
            }
        }
        if (!parentAlive && !isShuttingDown) {
            console.warn(`[WhatsApp] Supervisor PID ${PARENT_PID} disappeared; shutting down.`);
            setTimeout(forceTerminateOwnProcessTree, Math.max(5000, SHUTDOWN_TIMEOUT_MS + 5000));
            void gracefulShutdown(0);
        }
    }, 500);
    parentWatchdogTimer.unref?.();
}

async function clearAllTypingStates(contextLabel = 'cleanup') {
    const activeChats = Array.from(activeTypingChats.entries());
    activeTypingChats.clear();

    for (const [chatId, typingChat] of activeChats) {
        if (!typingChat || typeof typingChat.clearState !== 'function') {
            continue;
        }
        try {
            await typingChat.clearState();
            console.log(`[WhatsApp] Cleared typing indicator for chat ${redactIdentifier(chatId)} during ${contextLabel}`);
        } catch (error) {
            console.error(`[WhatsApp] Error clearing typing state for ${redactIdentifier(chatId)} during ${contextLabel}:`, error);
        }
    }
}

const CHROMIUM_EXECUTABLE_PATH = resolveChromiumExecutable(RAW_CHROMIUM_EXECUTABLE_PATH);
if (RAW_CHROMIUM_EXECUTABLE_PATH && !CHROMIUM_EXECUTABLE_PATH) {
    console.warn(`[WhatsApp] Ignoring invalid PUPPETEER_EXECUTABLE_PATH: ${RAW_CHROMIUM_EXECUTABLE_PATH}`);
}

console.log(`[WhatsApp Client] Starting with WebSocket port ${WEBSOCKET_PORT}`);
console.log(`[WhatsApp Client] Browser headless mode: ${WHATSAPP_BROWSER_HEADLESS}`);

function currentServiceStatus() {
    if (clientReady) return 'connected';
    if (isReconnecting) return 'recovering';
    if (currentQR) return 'pairing';
    if (authReadyStalled) return 'auth_stalled';
    if (clientAuthenticated) return 'authenticated';
    return 'starting';
}

function clearAuthReadyWatchdog() {
    if (authReadyWatchdogTimer) {
        clearTimeout(authReadyWatchdogTimer);
        authReadyWatchdogTimer = null;
    }
}

function resetAuthProgressState() {
    clientAuthenticated = false;
    lastAuthenticatedAt = null;
    authReadyStalled = false;
    clearAuthReadyWatchdog();
}

function startAuthReadyWatchdog() {
    clearAuthReadyWatchdog();
    if (!Number.isFinite(AUTH_READY_TIMEOUT_MS) || AUTH_READY_TIMEOUT_MS <= 0) {
        return;
    }
    authReadyWatchdogTimer = setTimeout(async () => {
        authReadyWatchdogTimer = null;
        if (isShuttingDown || isRestartingClient || clientReady || !clientAuthenticated) {
            return;
        }
        if (authReadyRestartAttempts >= MAX_AUTH_READY_RESTARTS) {
            if (!useFreshWebVersion) {
                useFreshWebVersion = true;
                authReadyRestartAttempts = 0;
                console.warn(
                    '[WhatsApp] Authenticated session is stuck before ready; retrying with fresh WhatsApp Web HTML while preserving LocalAuth.'
                );
                try {
                    await restartClient();
                } catch (error) {
                    console.error('[WhatsApp] Error restarting with fresh WhatsApp Web HTML:', errorMessage(error));
                    startAuthReadyWatchdog();
                }
                return;
            }
            console.warn(
                `[WhatsApp] Authenticated session did not become ready after ${AUTH_READY_TIMEOUT_MS}ms and ${authReadyRestartAttempts} browser restart attempt(s).`
            );
            authReadyStalled = true;
            broadcastToWebSockets({
                event: 'status',
                data: 'auth_stalled'
            });
            return;
        }
        authReadyRestartAttempts++;
        console.warn(
            `[WhatsApp] Authenticated session has not reached ready after ${AUTH_READY_TIMEOUT_MS}ms; restarting browser client (${authReadyRestartAttempts}/${MAX_AUTH_READY_RESTARTS})`
        );
        broadcastToWebSockets({
            event: 'status',
            data: 'recovering'
        });
        try {
            await restartClient();
        } catch (error) {
            console.error('[WhatsApp] Error restarting browser after authenticated readiness timeout:', error);
            startAuthReadyWatchdog();
        }
    }, AUTH_READY_TIMEOUT_MS);
}

function errorMessage(error) {
    if (!error) return '';
    return error.message || String(error);
}

function hasUsablePuppeteerPage() {
    const page = client?.pupPage;
    if (!page) {
        return false;
    }
    try {
        if (typeof page.isClosed === 'function' && page.isClosed()) {
            return false;
        }
    } catch (_) {
        return false;
    }
    return typeof page.evaluate === 'function';
}

function isPageOperationTimeout(error) {
    const message = errorMessage(error);
    return /Runtime\.callFunctionOn timed out/i.test(message) || /Protocol(?:Error| error).*timed out/i.test(message);
}

function isFatalSessionError(error) {
    const message = errorMessage(error);
    return [
        /Session closed/i,
        /Most likely the page has been closed/i,
        /Target closed/i,
        /Connection closed/i,
        /Page crashed/i,
        /Attempted to use detached Frame/i,
        /Cannot read properties of null \(reading 'evaluate'\)/i,
    ].some((pattern) => pattern.test(message));
}

function isRecoverablePageError(error) {
    const message = errorMessage(error);
    return (
        isFatalSessionError(error) ||
        isPageOperationTimeout(error) ||
        /Protocol error/i.test(message) ||
        /Execution context was destroyed/i.test(message) ||
        /navigation/i.test(message)
    );
}

function shouldLogChannelMetadataSkip() {
    const now = Date.now();
    if (
        lastChannelMetadataSkipLogAt > 0 &&
        Number.isFinite(CHANNEL_METADATA_SKIP_LOG_INTERVAL_MS) &&
        CHANNEL_METADATA_SKIP_LOG_INTERVAL_MS > 0 &&
        now - lastChannelMetadataSkipLogAt < CHANNEL_METADATA_SKIP_LOG_INTERVAL_MS
    ) {
        return false;
    }
    lastChannelMetadataSkipLogAt = now;
    return true;
}

function logObfuscatedDiagnostic(message) {
    if (OBFUSCATED_DIAGNOSTIC_LOGGING) {
        console.log(message);
    }
}

async function readClientStateSnapshot({ allowWhenNotReady = false } = {}) {
    if (!client) {
        return null;
    }
    if (!allowWhenNotReady && !clientReady) {
        return (lastState || currentServiceStatus()).toString().toUpperCase();
    }
    if (!hasUsablePuppeteerPage()) {
        return clientReady ? 'RECOVERING' : (lastState || currentServiceStatus()).toString().toUpperCase();
    }
    try {
        return await client.getState();
    } catch (error) {
        if (isPageOperationTimeout(error)) {
            return lastState || (clientReady ? 'CONNECTED' : currentServiceStatus().toString().toUpperCase());
        }
        if (isRecoverablePageError(error)) {
            return clientReady ? 'RECOVERING' : (lastState || currentServiceStatus()).toString().toUpperCase();
        }
        return `error:${errorMessage(error)}`;
    }
}

function getFallbackContactName(msg) {
    return (
        msg?._data?.notifyName ||
        msg?._data?.sender?.pushname ||
        msg?._data?.sender?.formattedName ||
        msg?._data?.from?.user ||
        msg?.to?.split?.('@')?.[0] ||
        msg?.from?.split?.('@')?.[0] ||
        phoneNumber ||
        null
    );
}

function pruneRecentMessages(store) {
    const now = Date.now();
    for (const [existingKey, expiresAt] of store.entries()) {
        if (expiresAt <= now) {
            store.delete(existingKey);
        }
    }
}

function hasRecentMessage(store, fingerprint) {
    const key = String(fingerprint || '').trim();
    if (!key) {
        return false;
    }
    pruneRecentMessages(store);
    return store.has(key);
}

function rememberRecentMessage(store, fingerprint, ttlMs = INBOUND_MESSAGE_DEDUP_TTL_MS) {
    const key = String(fingerprint || '').trim();
    if (!key) {
        return;
    }
    pruneRecentMessages(store);
    store.set(key, Date.now() + ttlMs);
}

function rememberInboundMessage(fingerprint) {
    if (hasRecentMessage(recentInboundMessageIds, fingerprint)) {
        return true;
    }
    rememberRecentMessage(recentInboundMessageIds, fingerprint);
    return false;
}

function rememberOutboundMessage(fingerprint) {
    rememberRecentMessage(recentOutboundMessageIds, fingerprint);
}

function isTrackedOutboundMessage(fingerprint) {
    return hasRecentMessage(recentOutboundMessageIds, fingerprint);
}

function isSelfChatAddress(chatId) {
    const normalizedChatId = normalizeWhatsAppAddress(chatId);
    return Boolean(
        normalizedChatId &&
        (
            normalizedChatId === normalizeWhatsAppAddress(phoneNumber) ||
            normalizedChatId === normalizeWhatsAppAddress(lastSelfChatId)
        )
    );
}

async function resolveMessageContact(msg) {
    if (!msg || typeof msg.getContact !== 'function') {
        return null;
    }
    try {
        return await msg.getContact();
    } catch (error) {
        if (isFatalSessionError(error)) {
            throw error;
        }
        if (isIgnorableContactMetadataLookupError(error)) {
            logObfuscatedDiagnostic(
                `[WhatsApp] Contact lookup skipped optional metadata: ${errorMessage(error)}`
            );
            return null;
        }
        console.warn('[WhatsApp] Contact lookup failed, continuing without contact metadata:', errorMessage(error));
        return null;
    }
}

async function resolveChatContact(chat) {
    if (!chat || typeof chat.getContact !== 'function') {
        return null;
    }
    try {
        return await chat.getContact();
    } catch (error) {
        if (isFatalSessionError(error)) {
            throw error;
        }
        if (isIgnorableContactMetadataLookupError(error)) {
            logObfuscatedDiagnostic(
                `[WhatsApp] Chat contact lookup skipped optional metadata: ${errorMessage(error)}`
            );
            return null;
        }
        console.warn('[WhatsApp] Chat contact lookup failed, continuing without chat contact metadata:', errorMessage(error));
        return null;
    }
}

async function resolveMessageChat(msg, sourceEvent) {
    if (!msg || typeof msg.getChat !== 'function') {
        return null;
    }
    try {
        return await msg.getChat();
    } catch (error) {
        if (isFatalSessionError(error)) {
            throw error;
        }
        if (isIgnorableChannelMetadataLookupError(error)) {
            if (shouldLogChannelMetadataSkip()) {
                logObfuscatedDiagnostic(
                    `[WhatsApp] Skipping ${sourceEvent} chat lookup for unsupported channel metadata from=${redactIdentifier(msg.from || '-')} to=${redactIdentifier(msg.to || '-')} remote=${redactIdentifier(msg?.id?.remote || msg?._data?.id?.remote || '-')}: ${errorMessage(error)}`
                );
            }
            return null;
        }
        if (shouldLogChannelMetadataSkip()) {
            logObfuscatedDiagnostic(
                `[WhatsApp] Chat metadata unavailable, continuing with message metadata: ${errorMessage(error)}`
            );
        }
        return null;
    }
}

function discoverSelfChatId() {
    if (!client || lastSelfChatId) {
        return lastSelfChatId;
    }

    const wid = client.info?.wid || client.info?.me;
    const serializedWid = String(wid?._serialized || '').trim();
    const user = String(wid?.user || phoneNumber || '').trim();
    const server = String(wid?.server || 'c.us').trim();
    const discoveredChatId = serializedWid || (user ? `${user}@${server}` : '');
    if (discoveredChatId) {
        rememberSelfChatId(discoveredChatId);
        console.log(`[WhatsApp] Using authenticated self chat id: ${redactIdentifier(discoveredChatId)}`);
    }
    return lastSelfChatId;
}

function rememberSelfChatId(chatId) {
    const resolvedChatId = String(chatId || '').trim();
    if (resolvedChatId) {
        lastSelfChatId = resolvedChatId;
    }
    return lastSelfChatId;
}

function getActiveTypingChatEntry(chatId) {
    const requestedChatId = String(chatId || '').trim();
    if (!requestedChatId) {
        return null;
    }
    if (activeTypingChats.has(requestedChatId)) {
        return { key: requestedChatId, chat: activeTypingChats.get(requestedChatId) };
    }
    const normalizedRequested = normalizeWhatsAppAddress(requestedChatId);
    for (const [key, chat] of activeTypingChats.entries()) {
        const candidates = [
            key,
            chat?.id?._serialized,
            chat?.id?.user,
        ].map((value) => normalizeWhatsAppAddress(value)).filter(Boolean);
        if (normalizedRequested && candidates.includes(normalizedRequested)) {
            return { key, chat };
        }
    }
    if (
        lastSelfChatId &&
        normalizedRequested &&
        normalizeWhatsAppAddress(lastSelfChatId) === normalizedRequested &&
        activeTypingChats.has(lastSelfChatId)
    ) {
        return { key: lastSelfChatId, chat: activeTypingChats.get(lastSelfChatId) };
    }
    return null;
}

async function resolveTypingChat(chatId) {
    const requestedChatId = String(chatId || '').trim();
    if (!requestedChatId || !clientReady || !client) {
        return null;
    }
    const activeEntry = getActiveTypingChatEntry(requestedChatId);
    if (activeEntry?.chat) {
        return activeEntry.chat;
    }

    const candidateIds = [];
    const seen = new Set();
    const addCandidate = (value) => {
        const candidate = String(value || '').trim();
        if (!candidate || seen.has(candidate)) {
            return;
        }
        seen.add(candidate);
        candidateIds.push(candidate);
    };

    const normalizedRequested = normalizeWhatsAppAddress(requestedChatId);
    if (
        lastSelfChatId &&
        normalizedRequested &&
        normalizeWhatsAppAddress(lastSelfChatId) === normalizedRequested
    ) {
        addCandidate(lastSelfChatId);
    }
    addCandidate(requestedChatId);
    if (requestedChatId && !requestedChatId.includes('@')) {
        addCandidate(`${requestedChatId}@c.us`);
    }

    for (const candidateId of candidateIds) {
        try {
            const chat = await client.getChatById(candidateId);
            if (chat) {
                return chat;
            }
        } catch (error) {
            if (isFatalSessionError(error)) {
                throw error;
            }
        }
    }
    return null;
}

async function startTypingForChat(chatId) {
    const requestedChatId = String(chatId || '').trim();
    if (!requestedChatId) {
        return null;
    }
    const chat = await resolveTypingChat(requestedChatId);
    if (!chat) {
        return null;
    }
    await chat.sendStateTyping();
    const resolvedChatId = chat?.id?._serialized || requestedChatId;
    rememberSelfChatId(resolvedChatId);
    activeTypingChats.set(resolvedChatId, chat);
    return resolvedChatId;
}

async function stopTypingForChat(chatId) {
    const entry = getActiveTypingChatEntry(chatId);
    if (!entry?.chat) {
        return false;
    }
    await entry.chat.clearState();
    activeTypingChats.delete(entry.key);
    return true;
}

async function withTimeout(operation, timeoutMs, label) {
    let timeoutHandle;
    const timeoutPromise = new Promise((_, reject) => {
        timeoutHandle = setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs);
    });
    try {
        return await Promise.race([operation, timeoutPromise]);
    } finally {
        clearTimeout(timeoutHandle);
    }
}

async function closeWebSocketServer() {
    if (!wss) {
        return;
    }
    const server = wss;
    wss = null;
    for (const ws of Array.from(wsConnections)) {
        try {
            ws.close();
        } catch (_) {}
    }
    wsConnections.clear();
    try {
        await withTimeout(
            new Promise((resolve) => server.close(resolve)),
            SHUTDOWN_TIMEOUT_MS,
            'WebSocket server close',
        );
    } catch (error) {
        console.error('[WhatsApp] Error closing WebSocket server:', error);
    }
}

async function destroyClientSafely(reason = 'shutdown') {
    const activeClient = client;
    client = null;
    if (!activeClient) {
        return;
    }
    try {
        await withTimeout(activeClient.destroy(), SHUTDOWN_TIMEOUT_MS, `client.destroy (${reason})`);
        console.log(`[WhatsApp] Client destroyed for ${reason}`);
    } catch (error) {
        console.error(`[WhatsApp] Client destroy failed during ${reason}:`, error);
    }
}

async function buildStatusSnapshot() {
    const snapshot = {
        ready: clientReady,
        phone_number: phoneNumber,
        self_chat_id: lastSelfChatId,
        qr_available: currentQR !== null,
        current_service_status: currentServiceStatus(),
        authenticated: clientAuthenticated,
        authenticated_at: lastAuthenticatedAt,
        auth_ready_restart_attempts: authReadyRestartAttempts,
        auth_ready_stalled: authReadyStalled,
        last_state: lastState,
        is_reconnecting: isReconnecting,
        ws_connection_count: wsConnections.size,
        client_initialized: client !== null,
    };

    if (client) {
        snapshot.web_state = await readClientStateSnapshot({ allowWhenNotReady: false });
        try {
            snapshot.info_user = client.info?.wid?.user || null;
        } catch (_) {
            snapshot.info_user = null;
        }
    } else {
        snapshot.web_state = null;
        snapshot.info_user = null;
    }

    return snapshot;
}

// Initialize WebSocket Server
function initializeWebSocketServer() {
    try {
        console.log(`[WebSocket] Attempting to create WebSocket server on port ${WEBSOCKET_PORT}`);
        
        wss = new WebSocketServer({ 
            port: WEBSOCKET_PORT,
            host: 'localhost',
            // Enable compression to reduce payload size for JSON messages
            perMessageDeflate: {
                threshold: 1024,
            }
        });
        
        // Handle server-level errors
        wss.on('error', (error) => {
            console.error('[WebSocket] Server error:', error);
            console.error('[WebSocket] Error code:', error.code);
            console.error('[WebSocket] Error message:', error.message);
            
            if (error.code === 'EADDRINUSE') {
                console.error(`[WebSocket] Port ${WEBSOCKET_PORT} is already in use`);
            } else if (error.code === 'EACCES') {
                console.error(`[WebSocket] Permission denied for port ${WEBSOCKET_PORT}`);
            }
            
            // Exit process on server error to allow restart
            process.exit(1);
        });
        
        // Handle successful server listening
        wss.on('listening', () => {
            console.log(`[WebSocket] Server successfully listening on port ${WEBSOCKET_PORT}`);
        });
        
        wss.on('connection', (ws, request) => {
            const isProbe = request?.url?.includes('probe=1');
            if (!isProbe) {
                console.log('[WebSocket] New connection established');
            }
            wsConnections.add(ws);
            
            // Send current status to new connection
            const statusMessage = {
                event: 'status',
                data: currentServiceStatus()
            };
            ws.send(JSON.stringify(statusMessage));
            
            // Send current QR if available and not ready
            if (currentQR && !clientReady) {
                const qrMessage = {
                    event: 'qr',
                    data: currentQR
                };
                ws.send(JSON.stringify(qrMessage));
            }
            
            // Send phone number if available
            if (phoneNumber) {
                const phoneMessage = {
                    event: 'phone_number',
                    data: phoneNumber
                };
                ws.send(JSON.stringify(phoneMessage));
            }
            
            // Handle incoming WebSocket messages
            ws.on('message', async (message) => {
                try {
                    const data = JSON.parse(message.toString());
                    await handleWebSocketCommand(data, ws);
                } catch (error) {
                    console.error('[WebSocket] Error parsing message:', error);
                }
            });
            
            ws.on('close', () => {
                if (!isProbe) {
                    console.log('[WebSocket] Connection closed');
                }
                wsConnections.delete(ws);
            });
            
            ws.on('error', (error) => {
                if (!isProbe) {
                    console.error('[WebSocket] Connection error:', error);
                }
                wsConnections.delete(ws);
            });

            if (isProbe) {
                try {
                    ws.close();
                } catch (_) {}
                return;
            }
        });
        
        console.log(`[WebSocket] WebSocket server created, waiting for listening event...`);
        
    } catch (error) {
        console.error('[WebSocket] Failed to create WebSocket server:', error);
        console.error('[WebSocket] Error details:', error.message);
        console.error('[WebSocket] Stack trace:', error.stack);
        
        // Exit process on initialization error
        process.exit(1);
    }
}

// Handle WebSocket commands from Python service
async function handleWebSocketCommand(data, ws) {
    const { action, data: payload } = data;

    const sendCommandResult = (success, extra = {}) => {
        const commandId = payload && (payload.commandId || payload.command_id);
        if (!commandId) {
            return;
        }
        try {
            ws.send(JSON.stringify({
                event: 'command_result',
                data: {
                    action,
                    commandId,
                    success: !!success,
                    ...extra
                }
            }));
        } catch (error) {
            console.warn('[WebSocket] Failed to send command_result:', errorMessage(error));
        }
    };
    
    switch (action) {
        case 'send_message':
            // Clear typing state before sending message if we have an active typing chat
            try {
                const typingChatId = payload.chatId || payload.to;
                const clearedTyping = await stopTypingForChat(typingChatId);
                if (clearedTyping) {
                    console.log(`[WhatsApp] Stopped typing indicator before sending response to ${redactIdentifier(typingChatId)}`);
                }
            } catch (error) {
                console.warn('[WhatsApp] Could not clear typing state before sending response:', errorMessage(error));
            }
            
            await sendMessage(payload.chatId || payload.to, payload.message);
            break;

        case 'send_audio':
            // Clear typing state before sending the voice note, mirroring send_message.
            try {
                const audioTypingChatId = payload.chatId || payload.to;
                const clearedAudioTyping = await stopTypingForChat(audioTypingChatId);
                if (clearedAudioTyping) {
                    console.log(`[WhatsApp] Stopped typing indicator before sending voice note to ${redactIdentifier(audioTypingChatId)}`);
                }
            } catch (error) {
                console.warn('[WhatsApp] Could not clear typing state before voice note:', errorMessage(error));
            }
            await sendAudioMessage(
                payload.chatId || payload.to,
                payload.data,
                payload.mimetype,
                payload.asVoice !== false,
                payload.caption,
            );
            break;

        case 'send_media':
            try {
                const mediaTypingChatId = payload.chatId || payload.to;
                const clearedMediaTyping = await stopTypingForChat(mediaTypingChatId);
                if (clearedMediaTyping) {
                    console.log(`[WhatsApp] Stopped typing indicator before sending media to ${redactIdentifier(mediaTypingChatId)}`);
                }
                const sentMessage = await sendMediaMessage(
                    payload.chatId || payload.to,
                    payload.data,
                    payload.mimetype,
                    payload.filename,
                    payload.caption,
                    payload.hd !== false,
                );
                if (sentMessage) {
                    sendCommandResult(true, {
                        messageId: sentMessage.id ? (sentMessage.id._serialized || sentMessage.id.id || null) : null
                    });
                } else {
                    sendCommandResult(false, { error: 'sendMediaMessage returned false' });
                }
            } catch (error) {
                console.warn('[WhatsApp] Could not send media:', errorMessage(error));
                sendCommandResult(false, { error: errorMessage(error) });
            }
            break;

        case 'get_status':
            const status = {
                event: 'status_response',
                data: await buildStatusSnapshot()
            };
            ws.send(JSON.stringify(status));
            break;

        case 'set_server_name': {
            const requestedName = String((payload && (payload.serverName || payload.server_name)) || '').trim();
            if (requestedName) {
                SERVER_NAME = requestedName.slice(0, 128);
            }
            break;
        }
            
        case 'restart_client':
            await restartClient();
            break;

        case 'shutdown':
            console.log('[WhatsApp] Received graceful shutdown command from Python service');
            await gracefulShutdown(0);
            break;
            
        case 'stop_typing':
            // Stop typing indicator if active
            try {
                const chatIdToStop = payload.chatId;
                const stopped = await stopTypingForChat(chatIdToStop);
                if (stopped) {
                    console.log(`[WhatsApp] Stopped typing indicator via WebSocket command for chat: ${redactIdentifier(chatIdToStop)}`);
                }
            } catch (error) {
                console.warn('[WhatsApp] Error stopping typing indicator:', errorMessage(error));
            }
            break;
            
        case 'start_typing':
            // Start typing indicator for a specific chat
            try {
                const chatId = payload.chatId;
                if (!chatId) {
                    console.warn('[WhatsApp] start_typing command missing chatId');
                    break;
                }
                if (!clientReady) {
                    console.warn('[WhatsApp] Cannot start typing indicator: client not ready');
                    break;
                }
                const resolvedTypingChatId = await startTypingForChat(chatId);
                if (!resolvedTypingChatId) {
                    console.log(`[WhatsApp] Skipping typing indicator because no live chat context was resolved for: ${redactIdentifier(chatId)}`);
                    break;
                }
                console.log(`[WhatsApp] Started typing indicator for chat: ${redactIdentifier(resolvedTypingChatId)}`);
            } catch (error) {
                console.warn('[WhatsApp] Error starting typing indicator:', errorMessage(error));
            }
            break;
            
        default:
            console.log(`[WebSocket] Unknown action: ${action}`);
    }
}

// Broadcast message to all WebSocket connections
function broadcastToWebSockets(message) {
    const messageStr = JSON.stringify(message);
    wsConnections.forEach(ws => {
        if (ws.readyState === ws.OPEN) {
            try {
                ws.send(messageStr);
            } catch (error) {
                console.error('[WebSocket] Error sending message:', error);
                wsConnections.delete(ws);
            }
        }
    });
}

// Ensure uploads directory for WhatsApp media exists
function ensureUploadsDir() {
    try {
        const uploadsRoot = process.env.UPLOADS_DIR || path.resolve(__dirname, '../../uploads');
        const whatsappDir = path.join(uploadsRoot, 'whatsapp');
        if (!fs.existsSync(uploadsRoot)) {
            fs.mkdirSync(uploadsRoot, { recursive: true });
        }
        if (!fs.existsSync(whatsappDir)) {
            fs.mkdirSync(whatsappDir, { recursive: true });
        }
        return whatsappDir;
    } catch (e) {
        console.error('[WhatsApp] Failed to ensure uploads directory:', e);
        return null;
    }
}

// Save base64 media to disk and return file path
function saveMediaToUploads(media, preferredName, chatName) {
    try {
        const whatsappDir = ensureUploadsDir();
        if (!whatsappDir) return null;
        const ts = Date.now();
        const baseName = preferredName || 'attachment';
        // Pick extension from mimetype when filename missing
        let ext = '';
        if (media?.mimetype && typeof media.mimetype === 'string') {
            const mt = media.mimetype.toLowerCase();
            if (mt.includes('jpeg')) ext = '.jpg';
            else if (mt.includes('png')) ext = '.png';
            else if (mt.includes('gif')) ext = '.gif';
            else if (mt.includes('mp4')) ext = '.mp4';
            else if (mt.includes('webm')) ext = '.webm';
            else if (mt.includes('mp3')) ext = '.mp3';
            else if (mt.includes('wav')) ext = '.wav';
            else if (mt.includes('pdf')) ext = '.pdf';
        }
        const safeChat = (chatName || '').toString().replace(/[^a-zA-Z0-9_-]+/g, '');
        const fname = `${ts}_${safeChat || 'whatsapp'}_${baseName}${ext}`;
        const targetPath = path.join(whatsappDir, fname);
        const buf = Buffer.from(media.data, 'base64');
        fs.writeFileSync(targetPath, buf);
        console.log(`[WhatsApp] Saved media to WhatsApp uploads (${buf.length} bytes)`);
        return targetPath;
    } catch (e) {
        console.error('[WhatsApp] Failed to save media to uploads:', e);
        return null;
    }
}

// Initialize WhatsApp Client
function initializeWhatsAppClient() {
    // Prefer a cached WhatsApp Web version to stabilize against deprecations
    const resolveCachedWebVersion = (cacheDir) => {
        try {
            if (!fs.existsSync(cacheDir)) return undefined;
            const files = fs.readdirSync(cacheDir).filter(f => f.endsWith('.html'));
            if (files.length === 0) return undefined;
            const withStat = files.map(f => ({ f, mtime: fs.statSync(path.join(cacheDir, f)).mtimeMs }));
            withStat.sort((a, b) => b.mtime - a.mtime);
            if (
                Number.isFinite(WHATSAPP_WEB_CACHE_MAX_AGE_MS) &&
                WHATSAPP_WEB_CACHE_MAX_AGE_MS > 0 &&
                Date.now() - withStat[0].mtime > WHATSAPP_WEB_CACHE_MAX_AGE_MS
            ) {
                console.warn('[WhatsApp] Ignoring stale WhatsApp Web cache; preserving LocalAuth and fetching a fresh page.');
                return undefined;
            }
            return withStat[0].f.replace(/\.html$/, '');
        } catch (e) {
            return undefined;
        }
    };

    const cachedVersion = useFreshWebVersion ? undefined : resolveCachedWebVersion(CACHE_PATH);

    client = new Client({
        authStrategy: new LocalAuth({ clientId: DEVICE_NAME, dataPath: AUTH_DATA_PATH }),
        takeoverOnConflict: true,
        takeoverTimeoutMs: 2000,
        deviceName: DEVICE_NAME,
        browserName: WEBKIT_EXECUTABLE ? 'Safari' : 'Chrome',
        ...(WEBKIT_EXECUTABLE ? { userAgent: false, webVersionCache: { type: 'none' } }
            : { webVersionCache: { type: 'local', path: CACHE_PATH }, ...(cachedVersion ? { webVersion: cachedVersion } : {}) }),
        puppeteer: {
            ...(CHROMIUM_EXECUTABLE_PATH ? { executablePath: CHROMIUM_EXECUTABLE_PATH } : {}),
            protocolTimeout: PUPPETEER_PROTOCOL_TIMEOUT_MS,
            timeout: PUPPETEER_LAUNCH_TIMEOUT_MS,
            headless: WHATSAPP_BROWSER_HEADLESS,
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                // '--single-process', // removed for stability.
                '--disable-gpu'
            ]
        }
    });

    installWWebJsRecoveryGuards(client, {
        isRecoverablePageError,
        onRecoverableNavigationError: (error) => {
            console.warn('[WhatsApp] Ignoring recoverable page-navigation race during reinjection:', errorMessage(error));
        },
        onAutomaticLogout: () => {
            console.warn('[WhatsApp] Preserving LocalAuth during automatic WWebJS recovery; explicit session reset removes it.');
        },
    });

    console.log('[WhatsApp] Initializing client...');

    // QR Code event
    client.on('qr', (qr) => {
        console.log('[WhatsApp] QR code received');
        resetAuthProgressState();
        authReadyRestartAttempts = 0;
        currentQR = qr;
        
        // Display QR in terminal
        qrcode.generate(qr, { small: true });
        
        // Broadcast QR to WebSocket connections
        broadcastToWebSockets({
            event: 'qr',
            data: qr
        });
        broadcastToWebSockets({
            event: 'status',
            data: 'pairing'
        });
    });

    // Authentication success
    client.on('authenticated', () => {
        console.log('[WhatsApp] Client authenticated successfully');
        currentQR = null; // Clear QR code after authentication
        clientAuthenticated = true;
        lastAuthenticatedAt = Date.now();
        authReadyStalled = false;
        reconnectAttempts = 0; // Reset reconnect attempts on successful auth
        broadcastToWebSockets({
            event: 'status',
            data: 'authenticated'
        });
        startAuthReadyWatchdog();
    });

    // Client ready
    client.on('ready', async () => {
        console.log('[WhatsApp] Client is ready and connected');
        
        // Check if this was a recovery operation
        const wasRecovering = isReconnecting && sessionRecoveryAttempts > 0;
        
        clientReady = true;
        clientAuthenticated = true;
        authReadyStalled = false;
        clearAuthReadyWatchdog();
        authReadyRestartAttempts = 0;
        reconnectAttempts = 0; // Reset reconnect attempts on ready
        sessionRecoveryAttempts = 0; // Reset session recovery attempts on ready
        isReconnecting = false;
        lastSuccessfulOperation = Date.now();
        
        // If this was a recovery, broadcast success
        if (wasRecovering) {
            console.log('[WhatsApp] Session recovery completed successfully');
            broadcastToWebSockets({
                event: 'session_recovery_completed',
                data: {
                    timestamp: Date.now()
                }
            });
        }
        
        // Start session health monitoring
        startSessionHealthMonitoring();
        
        // Get phone number
        try {
            const info = client.info;
            phoneNumber = info.wid.user;
            console.log(`[WhatsApp] Phone number: ${redactIdentifier(phoneNumber)}`);
            discoverSelfChatId();
            
            // Broadcast phone number
            broadcastToWebSockets({
                event: 'phone_number',
                data: phoneNumber
            });
        } catch (error) {
            console.error('[WhatsApp] Error getting phone number:', error);
        }
        
        // Broadcast ready status
        broadcastToWebSockets({
            event: 'status',
            data: 'connected'
        });
        
        // Broadcast state change to READY
        broadcastToWebSockets({
            event: 'state_changed',
            data: {
                state: 'READY',
                timestamp: Date.now()
            }
        });
    });

    // Authentication failure
    client.on('auth_failure', (msg) => {
        console.error('[WhatsApp] Authentication failed:', msg);
        clientReady = false;
        resetAuthProgressState();
        
        // Stop health monitoring
        stopSessionHealthMonitoring();
        
        broadcastToWebSockets({
            event: 'status',
            data: 'auth_failure'
        });
        
        if (isRestartingClient || isShuttingDown) {
            return;
        }

        // Attempt reconnection after auth failure
        scheduleReconnection('auth_failure');
    });

    // Disconnection event - handle reconnection
    client.on('disconnected', (reason) => {
        console.log('[WhatsApp] Client disconnected:', reason);
        clientReady = false;
        resetAuthProgressState();
        phoneNumber = null;
        lastSelfChatId = null;
        
        // Stop health monitoring
        stopSessionHealthMonitoring();
        
        broadcastToWebSockets({
            event: 'status',
            data: 'disconnected',
            reason: reason
        });
        
        if (isRestartingClient || isShuttingDown) {
            return;
        }

        // Attempt reconnection
        scheduleReconnection(reason);
    });

    // State change event - handle different WhatsApp states
    client.on('change_state', (state) => {
        console.log(`[WhatsApp] State changed: ${lastState} -> ${state}`);
        lastState = state;
        
        broadcastToWebSockets({
            event: 'state_changed',
            data: {
                state: state,
                timestamp: Date.now()
            }
        });
        
        // Handle specific states
        handleStateChange(state);
    });

    // Loading screen event
    client.on('loading_screen', (percent, message) => {
        console.log(`[WhatsApp] Loading: ${percent}% - ${message}`);
        
        broadcastToWebSockets({
            event: 'loading_screen',
            data: {
                percent: percent,
                message: message,
                timestamp: Date.now()
            }
        });
    });

    // Battery change event
    client.on('change_battery', (batteryInfo) => {
        console.log(`[WhatsApp] Battery changed:`, batteryInfo);
        
        broadcastToWebSockets({
            event: 'battery_changed',
            data: batteryInfo
        });
    });

    // Remote session saved event
    client.on('remote_session_saved', () => {
        console.log('[WhatsApp] Remote session saved');
        
        broadcastToWebSockets({
            event: 'remote_session_saved',
            data: {
                timestamp: Date.now()
            }
        });
    });

    // Utility: extract URLs from text for downstream processing
    function extractUrls(text) {
        try {
            if (!text || typeof text !== 'string') return [];
            const urlRegex = /(https?:\/\/[^\s]+|www\.[^\s]+|\b[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:\/[^\s]*)?)/g;
            const matches = text.match(urlRegex) || [];
            const normalized = matches.map(u => {
                const s = u.trim();
                if (s.startsWith('http://') || s.startsWith('https://')) return s;
                return `https://${s}`;
            });
            // unique
            return Array.from(new Set(normalized));
        } catch (e) {
            console.error('[WhatsApp] URL extraction failed:', e);
            return [];
        }
    }

    async function handlePotentialSelfMessage(msg, sourceEvent) {
        try {
            const fromMe = Boolean(
                msg?.fromMe === true ||
                msg?._data?.fromMe === true ||
                msg?.id?.fromMe === true ||
                msg?._data?.id?.fromMe === true
            );
            const pendingOutboundMedia = Boolean(
                msg?.hasMedia &&
                fromMe &&
                outboundMediaGuard.consume(
                    msg?.id?.remote ||
                    msg?._data?.id?.remote ||
                    msg?.to ||
                    msg?._data?.to
                )
            );
            if (pendingOutboundMedia) {
                rememberInboundMessage(messageFingerprint(msg));
                console.log(`[WhatsApp] Ignoring pending AutoYou outbound media event=${sourceEvent}`);
                return;
            }
            discoverSelfChatId();
            const chat = await resolveMessageChat(msg, sourceEvent);
            const chatContact = await resolveChatContact(chat);
            const classification = classifyWhatsAppMessage(msg, {
                phoneNumber,
                clientInfo: client?.info,
                chat,
                chatContact,
                knownSelfChatId: lastSelfChatId,
            });
            if (!classification.shouldProcess) {
                if (classification.fromMe) {
                    logObfuscatedDiagnostic(
                        `[WhatsApp] Ignoring non-self sent message event=${sourceEvent} from=${redactIdentifier(msg.from || '-')} to=${redactIdentifier(msg.to || '-')} chat=${redactIdentifier(chat?.id?._serialized || '-')} chatContactIsMe=${classification.chatContactIsMe ? 'true' : 'false'}`
                    );
                }
                return;
            }
            if (classification.fingerprint && isTrackedOutboundMessage(classification.fingerprint)) {
                console.log(`[WhatsApp] Ignoring tracked outbound self-chat message event=${sourceEvent} id=${classification.fingerprint}`);
                return;
            }
            if (
                msg.hasMedia &&
                outboundMediaGuard.consume(classification.normalizedChatId)
            ) {
                if (classification.fingerprint) {
                    rememberInboundMessage(classification.fingerprint);
                }
                console.log(`[WhatsApp] Ignoring pending AutoYou outbound media event=${sourceEvent}`);
                return;
            }
            if (classification.fingerprint && rememberInboundMessage(classification.fingerprint)) {
                console.log(`[WhatsApp] Ignoring duplicate self-chat message event=${sourceEvent} id=${classification.fingerprint}`);
                return;
            }

            const messageText = typeof msg.body === 'string' ? msg.body : '';
            
            // Check if message is a response from Pairing Router
            if (messageText.startsWith("/otp") || messageText.startsWith("/autopair_answer")) {
                // These are generated from Pairing Router, so ignore them.
                console.log('[WhatsApp] Ignoring message from Pairing Router');
                return;
            }
            
            // Accept the legacy device label too so a renamed server does not replay old replies.
            if (hasDeviceSignature(messageText, SERVER_NAME) || hasDeviceSignature(messageText, DEVICE_NAME)) {
                console.log('[WhatsApp] Ignoring message with server signature to prevent loop');
                return;
            }
            
            const contact = await resolveMessageContact(msg);
            
            console.log(`[WhatsApp] Processing user message ${messageTextSummary(messageText)} hasMedia=${msg.hasMedia ? 'true' : 'false'}`);
            
            // Prepare message data
            const messageData = {
                id: msg.id?._serialized,
                body: messageText,
                timestamp: msg.timestamp,
                fromMe: classification.fromMe,
                selfChat: classification.selfChat,
                sourceEvent,
                from: msg.from,
                to: msg.to,
                remoteChatId: chat?.id?._serialized || msg.id?.remote || msg?._data?.id?.remote || null,
                chatName: chat?.name || chat?.id?.user || null,
                contactName: chatContact?.name || chatContact?.pushname || contact?.name || contact?.pushname || getFallbackContactName(msg),
                hasMedia: !!msg.hasMedia,
                links: extractUrls(messageText)
            };
            rememberSelfChatId(messageData.remoteChatId || chat?.id?._serialized || messageData.to);
            
            // Handle media if present
            if (msg.hasMedia) {
                try {
                    const media = await msg.downloadMedia();
                    if (media) {
                        // Compute size from base64 length
                        const sizeBytes = media.data ? Buffer.byteLength(media.data, 'base64') : 0;
                        const chatName = messageData.chatName || null;
                        // If media is large, offload to disk and send a path reference
                        if (media.data && sizeBytes > MAX_WS_MEDIA_BYTES) {
                            const savedPath = saveMediaToUploads(media, media.filename, chatName);
                            if (savedPath) {
                                messageData.media = {
                                    mimetype: media.mimetype,
                                    filename: media.filename || 'attachment',
                                    path: savedPath,
                                    size: sizeBytes,
                                    caption: messageText,
                                };
                            } else {
                                // Fallback to sending data if save failed
                                messageData.media = {
                                    mimetype: media.mimetype,
                                    filename: media.filename,
                                    data: media.data,
                                    size: sizeBytes,
                                    caption: messageText,
                                };
                            }
                        } else {
                            // Small enough: send inline base64
                            messageData.media = {
                                mimetype: media.mimetype,
                                filename: media.filename,
                                data: media.data,
                                size: sizeBytes,
                                caption: messageText,
                            };
                        }
                    }
                } catch (error) {
                    console.error('[WhatsApp] Error downloading media:', error);
                    
                    // Check if this is a session closure error
                    if (isSessionClosedError(error)) {
                        console.log('[WhatsApp] Session closure detected during media download');
                        await handleSessionRecovery('media_download_session_error');
                        return;
                    }
                }
            }

            // Read receipts and typing are optional; metadata failures must not
            // prevent a verified self message from reaching the Python service.
            if (chat) {
                try {
                    await chat.sendSeen();
                    console.log('[WhatsApp] Sent read receipt');
                } catch (error) {
                    console.warn('[WhatsApp] Read receipt unavailable; continuing:', errorMessage(error));
                }
                try {
                    await chat.sendStateTyping();
                    console.log('[WhatsApp] Started typing indicator');
                    const typingChatId = messageData.remoteChatId || chat?.id?._serialized || messageData.to || null;
                    if (typingChatId) {
                        activeTypingChats.set(typingChatId, chat);
                    }
                } catch (error) {
                    console.warn('[WhatsApp] Typing indicator unavailable; continuing:', errorMessage(error));
                }
            }
            
            // 3. Broadcast message to WebSocket connections
            broadcastToWebSockets({
                event: 'message',
                data: messageData
            });
            
            // Update last successful operation timestamp
            lastSuccessfulOperation = Date.now();
            
            // Process the message with AI (This is handled via WebSocket , so not needed and can be programmed as a fallback to different HTTP Systems)
            // await processMessageWithAI(chat, messageText);
            
        } catch (error) {
            console.error('[WhatsApp] Error handling message:', error);
            
            // Check if this is a session closure error
            if (isSessionClosedError(error)) {
                console.log('[WhatsApp] Session closure detected during message handling');
                await handleSessionRecovery('message_handling_session_error');
            }
        }
    }

    // Message handling - accept Notes to Self traffic from either wwebjs event.
    client.on('message', async (msg) => {
        await handlePotentialSelfMessage(msg, 'message');
    });

    client.on('message_create', async (msg) => {
        await handlePotentialSelfMessage(msg, 'message_create');
    });

    // Start the client
    client.initialize().catch(error => {
        console.error('[WhatsApp] Client initialization error:', error);
    });
}

// Process message with AI and respond
async function processMessageWithAI(chat, messageText) {
    try {        
        // 1. Send read receipt (blue ticks)
        await chat.sendSeen();
        console.log('[WhatsApp] Sent read receipt');
        
        // 2. Start typing indicator
        await chat.sendStateTyping();
        console.log('[WhatsApp] Started typing indicator');
        
        // 3. Get AI response
        const aiResponse = await getAIResponse(messageText);
        
        // 4. Stop typing indicator
        await chat.clearState();
        console.log('[WhatsApp] Stopped typing indicator');
        
        // 5. Send response to self
        if (aiResponse && aiResponse.trim()) {
            await sendMessageToSelf(aiResponse);
        } else {
            console.log('[WhatsApp] No AI response to send');
        }
        
    } catch (error) {
        console.error('[WhatsApp] Error processing message with AI:', error);
        
        // Try to clear typing state and send error message
        try {
            await chat.clearState();
            await sendMessageToSelf('Sorry, I encountered an error processing your message.');
        } catch (clearError) {
            console.error('[WhatsApp] Error clearing state or sending error message:', clearError);
        }
    } finally {
        // Nothing for now.
    }
}

// Get AI response from chat API
async function getAIResponse(messageText) {
    try {
        const requestBody = {
            message: messageText,
            session_id: `whatsapp_${phoneNumber || 'unknown'}`,
            user_id: `whatsapp_${phoneNumber || 'unknown'}`
        };
        
        console.log('[WhatsApp] Calling chat API...');
        
        const response = await fetch(CHAT_API_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(requestBody),
            timeout: 30000
        });
        
        if (!response.ok) {
            throw new Error(`Chat API error: ${response.status} - ${response.statusText}`);
        }
        
        const data = await response.json();
        const aiMessage = data.response || '';
        
        console.log(`[WhatsApp] Received AI response ${messageTextSummary(aiMessage)}`);
        return aiMessage;
        
    } catch (error) {
        console.error('[WhatsApp] Error getting AI response:', error);
        return 'Sorry, I encountered an error while processing your request.';
    }
}

// Send message to self (user's own number)
async function sendMessageToSelf(message) {
    try {
        if (!phoneNumber) {
            console.error('[WhatsApp] Cannot send to self - phone number unknown');
            return false;
        }
        
        const chatId = lastSelfChatId || `${phoneNumber}@c.us`;
        message = message.trim();
        message += `\n~ ${SERVER_NAME}`;
        const sentMessage = await client.sendMessage(chatId, message);
        rememberOutboundMessage(messageFingerprint(sentMessage));
        
        console.log(`[WhatsApp] Sent message to self ${messageTextSummary(message)}`);
        return true;
        
    } catch (error) {
        console.error('[WhatsApp] Error sending message to self:', error);
        return false;
    }
}

// Send message to specific number
async function sendMessage(to, message) {
    try {
        if (!clientReady) {
            console.error('[WhatsApp] Cannot send message - client not ready');
            return false;
        }
        
        // Format phone number
        const chatId = to.includes('@') ? to : `${to}@c.us`;
        const sentMessage = await client.sendMessage(chatId, message);
        rememberOutboundMessage(messageFingerprint(sentMessage));
        
        console.log(`[WhatsApp] Sent message to recipient ${messageTextSummary(message)}`);
        return true;
        
    } catch (error) {
        console.error('[WhatsApp] Error sending message to recipient:', error);
        
        // Check if this is a session closure error
        if (isSessionClosedError(error)) {
            console.log('[WhatsApp] Session closure detected during message sending');
            await handleSessionRecovery('send_message_session_error');
        }
        
        return false;
    }
}

// Send an image/video/file attachment. WhatsApp Web supports the HD flag for
// image media; leaving it enabled is harmless for video/document fallbacks.
async function sendMediaMessage(to, base64Data, mimetype, filename, caption = '', hd = true) {
    const normalizedTarget = String(to || '');
    const chatId = normalizedTarget.includes('@') ? normalizedTarget : `${normalizedTarget}@c.us`;
    let outboundGuardToken = null;
    try {
        if (!clientReady) {
            console.error('[WhatsApp] Cannot send media - client not ready');
            return false;
        }
        if (!base64Data) {
            console.error('[WhatsApp] Cannot send media - no data provided');
            return false;
        }
        outboundGuardToken = isSelfChatAddress(chatId) ? outboundMediaGuard.arm(chatId) : null;
        const resolvedMime = mimetype || 'application/octet-stream';
        const resolvedFilename = filename || (resolvedMime.startsWith('video/') ? 'video.mp4' : 'image.jpg');
        const media = new MessageMedia(resolvedMime, base64Data, resolvedFilename);
        const options = {
            caption: caption || undefined,
            sendMediaAsHd: !!hd,
        };
        const sentMessage = await client.sendMessage(chatId, media, options);
        rememberOutboundMessage(messageFingerprint(sentMessage));
        if (outboundGuardToken) {
            outboundMediaGuard.release(outboundGuardToken);
        }
        console.log(`[WhatsApp] Sent media ${resolvedFilename} (${resolvedMime}) to recipient`);
        return sentMessage || true;
    } catch (error) {
        if (outboundGuardToken) {
            outboundMediaGuard.release(outboundGuardToken);
        }
        console.error('[WhatsApp] Error sending media to recipient:', error);
        if (isSessionClosedError(error)) {
            console.log('[WhatsApp] Session closure detected during media send');
            await handleSessionRecovery('send_media_session_error');
        }
        return false;
    }
}

// Send an audio file (base64) as a WhatsApp voice note (or plain audio attachment)
async function sendAudioMessage(to, base64Data, mimetype, asVoice = true, caption = '') {
    const normalizedTarget = String(to || '');
    const chatId = normalizedTarget.includes('@') ? normalizedTarget : `${normalizedTarget}@c.us`;
    let outboundGuardToken = null;
    try {
        if (!clientReady) {
            console.error('[WhatsApp] Cannot send audio - client not ready');
            return false;
        }
        if (!base64Data) {
            console.error('[WhatsApp] Cannot send audio - no audio data provided');
            return false;
        }
        outboundGuardToken = isSelfChatAddress(chatId) ? outboundMediaGuard.arm(chatId) : null;
        // WhatsApp voice notes must be OGG/Opus; fall back to the provided mimetype otherwise.
        const resolvedMime = mimetype || 'audio/ogg; codecs=opus';
        const media = new MessageMedia(resolvedMime, base64Data, 'voice-note.ogg');
        const sentMessage = await client.sendMessage(chatId, media, {
            sendAudioAsVoice: !!asVoice,
            caption: caption || undefined,
        });
        rememberOutboundMessage(messageFingerprint(sentMessage));
        if (outboundGuardToken) {
            outboundMediaGuard.release(outboundGuardToken);
        }
        console.log(`[WhatsApp] Sent ${asVoice ? 'voice note' : 'audio'} to recipient`);
        return true;
    } catch (error) {
        if (outboundGuardToken) {
            outboundMediaGuard.release(outboundGuardToken);
        }
        console.error('[WhatsApp] Error sending audio to recipient:', error);
        if (isSessionClosedError(error)) {
            console.log('[WhatsApp] Session closure detected during audio send');
            await handleSessionRecovery('send_audio_session_error');
        }
        return false;
    }
}

// Handle state changes with appropriate actions
function handleStateChange(state) {
    if (isRestartingClient || isShuttingDown) {
        return;
    }
    switch (state) {
        case 'CONFLICT':
            console.log('[WhatsApp] State: CONFLICT - Multiple sessions detected');
            // Handle conflict by attempting to restart
            scheduleReconnection('conflict');
            break;
            
        case 'CONNECTED':
            console.log('[WhatsApp] State: CONNECTED - Successfully connected');
            reconnectAttempts = 0;
            isReconnecting = false;
            break;
            
        case 'DEPRECATED_VERSION':
            console.log('[WhatsApp] State: DEPRECATED_VERSION - WhatsApp Web version is deprecated');
            // This might require updating the library
            break;
            
        case 'OPENING':
            console.log('[WhatsApp] State: OPENING - Opening WhatsApp Web');
            break;
            
        case 'PAIRING':
            console.log('[WhatsApp] State: PAIRING - Waiting for QR code scan');
            break;
            
        case 'PROXYBLOCK':
            console.log('[WhatsApp] State: PROXYBLOCK - Proxy blocked');
            scheduleReconnection('proxyblock');
            break;
            
        case 'SMB_TOS_BLOCK':
            console.log('[WhatsApp] State: SMB_TOS_BLOCK - Terms of service block');
            break;
            
        case 'TIMEOUT':
            console.log('[WhatsApp] State: TIMEOUT - Connection timeout');
            scheduleReconnection('timeout');
            break;
            
        case 'TOS_BLOCK':
            console.log('[WhatsApp] State: TOS_BLOCK - Terms of service block');
            break;
            
        case 'UNLAUNCHED':
            console.log('[WhatsApp] State: UNLAUNCHED - Client not launched');
            break;
            
        case 'UNPAIRED':
            console.log('[WhatsApp] State: UNPAIRED - Device not paired');
            break;
            
        case 'UNPAIRED_IDLE':
            console.log('[WhatsApp] State: UNPAIRED_IDLE - Device unpaired and idle');
            break;
            
        default:
            console.log(`[WhatsApp] State: ${state} - Unknown state`);
    }
}

// Schedule reconnection with exponential backoff
function scheduleReconnection(reason) {
    if (isShuttingDown || isRestartingClient) {
        console.log('[WhatsApp] Reconnection suppressed during controlled restart/shutdown');
        return;
    }
    if (isReconnecting) {
        console.log('[WhatsApp] Reconnection already in progress, skipping');
        return;
    }
    
    if (reconnectAttempts >= maxReconnectAttempts) {
        console.log(`[WhatsApp] Max reconnection attempts (${maxReconnectAttempts}) reached. Stopping reconnection attempts.`);
        broadcastToWebSockets({
            event: 'reconnection_failed',
            data: {
                reason: reason,
                attempts: reconnectAttempts,
                timestamp: Date.now()
            }
        });
        return;
    }
    
    reconnectAttempts++;
    isReconnecting = true;
    
    // Calculate delay with exponential backoff
    const delay = reconnectDelay * Math.pow(2, reconnectAttempts - 1);
    
    console.log(`[WhatsApp] Scheduling reconnection attempt ${reconnectAttempts}/${maxReconnectAttempts} in ${delay}ms due to: ${reason}`);
    
    broadcastToWebSockets({
        event: 'reconnection_scheduled',
        data: {
            reason: reason,
            attempt: reconnectAttempts,
            maxAttempts: maxReconnectAttempts,
            delay: delay,
            timestamp: Date.now()
        }
    });
    
    setTimeout(async () => {
        try {
            console.log(`[WhatsApp] Attempting reconnection ${reconnectAttempts}/${maxReconnectAttempts}`);
            await restartClient();
        } catch (error) {
            console.error('[WhatsApp] Error during scheduled reconnection:', error);
            isReconnecting = false;
            // Schedule another attempt if we haven't reached the limit
            if (reconnectAttempts < maxReconnectAttempts) {
                scheduleReconnection(`reconnection_error: ${error.message}`);
            }
        }
    }, delay);
}

// Enhanced restart client with better error handling
async function restartClient() {
    try {
        if (isRestartingClient) {
            console.log('[WhatsApp] Client restart already in progress');
            return;
        }
        console.log('[WhatsApp] Restarting client...');
        isRestartingClient = true;
        isShuttingDown = false;
        
        // Stop health monitoring
        stopSessionHealthMonitoring();
        
        broadcastToWebSockets({
            event: 'client_restarting',
            data: {
                timestamp: Date.now()
            }
        });

        await clearAllTypingStates('restart');

        await destroyClientSafely('restart');
        
        clientReady = false;
        resetAuthProgressState();
        phoneNumber = null;
        lastSelfChatId = null;
        currentQR = null;
        lastState = null;
        
        // Broadcast disconnected status
        broadcastToWebSockets({
            event: 'status',
            data: 'disconnected'
        });
        
        // Wait a bit before reinitializing
        await new Promise(resolve => setTimeout(resolve, 2000));
        
        // Reinitialize client
        initializeWhatsAppClient();
        isRestartingClient = false;
        
        console.log('[WhatsApp] Client restart initiated');
        
        // The isReconnecting flag will be reset in the 'ready' event handler
        
    } catch (error) {
        console.error('[WhatsApp] Error restarting client:', error);
        isReconnecting = false;
        isRestartingClient = false;
        sessionRecoveryAttempts = 0;
        throw error;
    }
}

// Graceful shutdown
async function gracefulShutdown(exitCode = 0) {
    console.log('[WhatsApp] Shutting down gracefully...');
    isShuttingDown = true;
    
    try {
        if (parentWatchdogTimer) {
            clearInterval(parentWatchdogTimer);
            parentWatchdogTimer = null;
        }
        // Stop health monitoring
        stopSessionHealthMonitoring();
        clearAuthReadyWatchdog();

        await clearAllTypingStates('shutdown');

        await destroyClientSafely('shutdown');
        await closeWebSocketServer();
        
        console.log('[WhatsApp] Shutdown complete');
        process.exit(exitCode);
        
    } catch (error) {
        console.error('[WhatsApp] Error during shutdown:', error);
        process.exit(1);
    }
}

// Handle process signals
process.on('SIGINT', () => { void gracefulShutdown(0); });
process.on('SIGTERM', () => { void gracefulShutdown(0); });

startParentWatchdog();

// Handle uncaught exceptions
process.on('uncaughtException', (error) => {
    console.error('[WhatsApp] Uncaught exception:', error);
    try {
        const msg = (error && error.message) ? error.message : String(error);
        if (isSessionClosedError(error)) {
            console.log('[WhatsApp] Treating uncaught exception as recoverable, scheduling reconnection');
            scheduleReconnection(`uncaught_exception: ${msg}`);
            return;
        }
    } catch (_) {}
    console.log('[WhatsApp] Uncaught exception not classified as session closure; continuing without shutdown');
});

process.on('unhandledRejection', (reason, promise) => {
    console.error('[WhatsApp] Unhandled rejection at:', promise, 'reason:', reason);
    try {
        const msg = (reason && reason.message) ? reason.message : String(reason);
        if (isSessionClosedError({ message: msg })) {
            console.log('[WhatsApp] Treating unhandled rejection as recoverable, scheduling reconnection');
            scheduleReconnection(`unhandled_rejection: ${msg}`);
            return;
        }
    } catch (_) {}
    console.log('[WhatsApp] Unhandled rejection not classified as session closure; continuing without shutdown');
});

// Start the application
console.log('[WhatsApp] Starting WhatsApp Web.js client with WebSocket server...');

// Initialize WebSocket server first
initializeWebSocketServer();

// Then initialize WhatsApp client
initializeWhatsAppClient();

console.log('[WhatsApp] Application started successfully');

// Enhanced error detection for session closure
function isSessionClosedError(error) {
    return isFatalSessionError(error);
}

// Check if client session is healthy
async function checkSessionHealth() {
    try {
        if (!client || !clientReady) {
            return false;
        }

        if (!hasUsablePuppeteerPage()) {
            console.log('[WhatsApp] Session health check failed - Puppeteer page is unavailable');
            return false;
        }

        const state = await readClientStateSnapshot({ allowWhenNotReady: true });
        if (!state || String(state).startsWith('error:') || state === 'RECOVERING') {
            console.log(`[WhatsApp] Session health check failed - invalid state snapshot: ${state}`);
            return false;
        }
        
        // Additional check - try to get client info
        const info = await client.info;
        if (!info) {
            console.log('[WhatsApp] Session health check failed - no client info');
            return false;
        }
        
        lastSuccessfulOperation = Date.now();
        return true;
        
    } catch (error) {
        console.error('[WhatsApp] Session health check failed:', error);
        
        if (isSessionClosedError(error)) {
            console.log('[WhatsApp] Session closure detected during health check');
            return false;
        }
        
        // For other errors, don't immediately trigger recovery
        // They might be temporary network issues
        console.log('[WhatsApp] Non-critical error during health check, continuing monitoring');
        return true;
    }
}

// Start session health monitoring
function startSessionHealthMonitoring() {
    // Clear existing interval
    if (sessionHealthCheckInterval) {
        clearInterval(sessionHealthCheckInterval);
    }
    
    // Check session health every 30 seconds
    sessionHealthCheckInterval = setInterval(async () => {
        if (!clientReady || isReconnecting) {
            return;
        }
        
        const isHealthy = await checkSessionHealth();
        if (!isHealthy) {
            console.log('[WhatsApp] Session health check failed - triggering recovery');
            await handleSessionRecovery('health_check_failed');
        }
    }, 30000);
    
    console.log('[WhatsApp] Session health monitoring started');
}

// Stop session health monitoring
function stopSessionHealthMonitoring() {
    if (sessionHealthCheckInterval) {
        clearInterval(sessionHealthCheckInterval);
        sessionHealthCheckInterval = null;
        console.log('[WhatsApp] Session health monitoring stopped');
    }
}

// Handle session recovery
async function handleSessionRecovery(reason) {
    if (isShuttingDown) {
        console.log('[WhatsApp] Session recovery suppressed during shutdown');
        return;
    }
    if (isReconnecting) {
        console.log('[WhatsApp] Session recovery already in progress');
        return;
    }
    
    if (sessionRecoveryAttempts >= maxSessionRecoveryAttempts) {
        console.log(`[WhatsApp] Max session recovery attempts (${maxSessionRecoveryAttempts}) reached`);
        // Reset session recovery attempts and fall back to full reconnection
        sessionRecoveryAttempts = 0;
        scheduleReconnection(`session_recovery_failed: ${reason}`);
        return;
    }
    
    sessionRecoveryAttempts++;
    isReconnecting = true;
    
    console.log(`[WhatsApp] Starting session recovery attempt ${sessionRecoveryAttempts}/${maxSessionRecoveryAttempts} due to: ${reason}`);
    
    broadcastToWebSockets({
        event: 'session_recovery_started',
        data: {
            reason: reason,
            attempt: sessionRecoveryAttempts,
            maxAttempts: maxSessionRecoveryAttempts,
            timestamp: Date.now()
        }
    });
    
    try {
        // Stop health monitoring during recovery
        stopSessionHealthMonitoring();
        
        // Mark client as not ready
        clientReady = false;
        
        // Broadcast status change
        broadcastToWebSockets({
            event: 'status',
            data: 'recovering'
        });
        
        // Request service restart from the WhatsApp service
        console.log('[WhatsApp] Requesting service restart for session recovery');
        broadcastToWebSockets({
            event: 'service_restart_request',
            data: {
                reason: `session_recovery: ${reason}`,
                attempt: sessionRecoveryAttempts,
                timestamp: Date.now()
            }
        });
        
        console.log('[WhatsApp] Service restart request sent successfully');
        
        // Keep recovery marked active until the Python service restarts us or the
        // ready handler confirms a healthy session. This suppresses duplicate
        // restart requests from message/message_create echoes while shutdown is pending.
        
    } catch (error) {
        console.error('[WhatsApp] Session recovery failed:', error);
        
        // Reset flags on error
        isReconnecting = false;
        
        broadcastToWebSockets({
            event: 'session_recovery_failed',
            data: {
                reason: reason,
                attempt: sessionRecoveryAttempts,
                error: error.message,
                timestamp: Date.now()
            }
        });
        
        // Try again if we haven't reached the limit
        if (sessionRecoveryAttempts < maxSessionRecoveryAttempts) {
            setTimeout(() => {
                handleSessionRecovery(`retry_after_error: ${error.message}`);
            }, 5000);
        } else {
            // Reset and fall back to full reconnection
            sessionRecoveryAttempts = 0;
            scheduleReconnection(`session_recovery_exhausted: ${reason}`);
        }
    }
}
