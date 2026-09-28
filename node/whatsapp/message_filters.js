// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-B-726c79207375627461736b20-d23f28a38b0c928673ff34c0

function normalizeWhatsAppAddress(value) {
    const raw = String(value ?? '').trim().toLowerCase();
    if (!raw) {
        return '';
    }
    const localPart = raw.split('@')[0].trim();
    if (!localPart) {
        return '';
    }
    const digitsOnly = localPart.replace(/\D+/g, '');
    return digitsOnly || localPart;
}

function addNormalizedAddress(targetSet, value) {
    const normalized = normalizeWhatsAppAddress(value);
    if (normalized) {
        targetSet.add(normalized);
    }
}

function buildOwnAddressSet({ phoneNumber, clientInfo } = {}) {
    const ownAddresses = new Set();
    addNormalizedAddress(ownAddresses, phoneNumber);
    addNormalizedAddress(ownAddresses, clientInfo?.wid?._serialized);
    addNormalizedAddress(ownAddresses, clientInfo?.wid?.user);
    return ownAddresses;
}

function serializedMessageId(message) {
    return String(
        message?.id?._serialized ||
        message?._data?.id?._serialized ||
        ''
    ).trim();
}

function messageFingerprint(message) {
    const serializedId = serializedMessageId(message);
    if (serializedId) {
        return serializedId;
    }
    const parts = [
        message?.timestamp,
        message?.from,
        message?.to,
        message?.body,
    ].map((part) => String(part ?? '').trim());
    if (parts.some(Boolean)) {
        return parts.join('|');
    }
    return '';
}

function hasDeviceSignature(messageText, deviceName) {
    const signature = `~ ${String(deviceName || '').trim()}`;
    return Boolean(deviceName) && String(messageText || '').trim().endsWith(signature);
}

function createOutboundMediaGuard(ttlMs = 30000) {
    const pending = new Set();

    function prune() {
        const now = Date.now();
        for (const token of pending) {
            if (token.expiresAt <= now) {
                pending.delete(token);
            }
        }
    }

    return {
        arm(chatId) {
            prune();
            const token = {
                chatId: normalizeWhatsAppAddress(chatId),
                expiresAt: Date.now() + ttlMs,
            };
            pending.add(token);
            return token;
        },
        release(token) {
            pending.delete(token);
        },
        consume(chatId) {
            prune();
            const normalizedChatId = normalizeWhatsAppAddress(chatId);
            for (const token of pending) {
                if (token.chatId === normalizedChatId) {
                    pending.delete(token);
                    return true;
                }
            }
            return false;
        },
    };
}

function isIgnorableChannelMetadataLookupError(error) {
    const message = String(error?.message || error || '');
    const stack = String(error?.stack || '');
    const combined = `${message}\n${stack}`;
    return (
        /Cannot read properties of undefined \(reading 'description'\)/i.test(message) &&
        /(Channel\._patch|structures[\\/]+Channel|ChatFactory\.create|Client\.getChatById)/i.test(combined)
    );
}

function isIgnorableContactMetadataLookupError(error) {
    const message = String(error?.message || error || '');
    return (
        /Data passed to getter must include an id property .* got undefined/i.test(message) ||
        /Cannot read properties of undefined \(reading ['"]_serialized['"]\)/i.test(message)
    );
}

function classifyWhatsAppMessage(message, { phoneNumber, clientInfo, chat, contact, chatContact, knownSelfChatId } = {}) {
    const ownAddresses = buildOwnAddressSet({ phoneNumber, clientInfo });
    const normalizedFrom = normalizeWhatsAppAddress(message?.from || message?._data?.from);
    const normalizedTo = normalizeWhatsAppAddress(message?.to || message?._data?.to);
    const normalizedChatId = normalizeWhatsAppAddress(
        chat?.id?._serialized ||
        chat?.id?.user ||
        message?.id?.remote ||
        message?._data?.id?.remote
    );
    const remoteCandidates = [
        chat?.id?._serialized,
        chat?.id?.user,
        message?.author,
        message?.id?.remote,
        message?._data?.author,
        message?._data?.id?.remote,
    ].map((value) => normalizeWhatsAppAddress(value)).filter(Boolean);
    const sameParticipantChat = Boolean(
        normalizedFrom &&
        normalizedTo &&
        normalizedFrom === normalizedTo
    );
    const chatContactIsMe = Boolean(chatContact?.isMe === true);
    const messageContactIsMe = Boolean(contact?.isMe === true);
    const ownsRemoteCandidate = remoteCandidates.some((candidate) => ownAddresses.has(candidate));
    const fromOwnAddress = Boolean(normalizedFrom && ownAddresses.has(normalizedFrom));
    const toOwnAddress = Boolean(normalizedTo && ownAddresses.has(normalizedTo));
    const chatIdMatchesOwnAddress = Boolean(normalizedChatId && ownAddresses.has(normalizedChatId));
    const normalizedKnownSelfChatId = normalizeWhatsAppAddress(knownSelfChatId);
    const knownSelfChat = Boolean(
        normalizedKnownSelfChatId &&
        normalizedChatId &&
        normalizedKnownSelfChatId === normalizedChatId
    );
    const directSelfNumberChat = Boolean(
        fromOwnAddress &&
        normalizedTo &&
        normalizedFrom === normalizedTo
    );
    const selfChat = Boolean(
        chatContactIsMe ||
        chatIdMatchesOwnAddress ||
        directSelfNumberChat ||
        knownSelfChat ||
        sameParticipantChat
    );
    const fromMe = Boolean(
        message?.fromMe === true ||
        message?._data?.fromMe === true ||
        message?.id?.fromMe === true ||
        message?._data?.id?.fromMe === true
    );

    return {
        fingerprint: messageFingerprint(message),
        fromMe,
        selfChat,
        shouldProcess: selfChat && fromMe,
        normalizedFrom,
        normalizedTo,
        normalizedChatId,
        chatContactIsMe,
        messageContactIsMe,
        ownsRemoteCandidate,
        fromOwnAddress,
        toOwnAddress,
        chatIdMatchesOwnAddress,
        knownSelfChat,
        remoteCandidates,
        ownAddresses: Array.from(ownAddresses),
    };
}

export {
    classifyWhatsAppMessage,
    createOutboundMediaGuard,
    hasDeviceSignature,
    isIgnorableChannelMetadataLookupError,
    isIgnorableContactMetadataLookupError,
    messageFingerprint,
    normalizeWhatsAppAddress,
    serializedMessageId,
};
