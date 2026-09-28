// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-C-746f20706179203130252061-f89261af94e07f45c8653707

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    classifyWhatsAppMessage,
    createOutboundMediaGuard,
    hasDeviceSignature,
    isIgnorableChannelMetadataLookupError,
    isIgnorableContactMetadataLookupError,
    messageFingerprint,
    normalizeWhatsAppAddress,
} from './message_filters.js';

test('recognizes the AutoYou device signature on text and media captions', () => {
    assert.equal(hasDeviceSignature('reply\n~ AutoYou-Test', 'AutoYou-Test'), true);
    assert.equal(hasDeviceSignature('reply', 'AutoYou-Test'), false);
});

test('consumes only the matching pending outbound media send', () => {
    const guard = createOutboundMediaGuard();
    const token = guard.arm('autoyou-test-self@lid');

    assert.equal(guard.consume('autoyou-test-self@lid'), true);
    assert.equal(guard.consume('autoyou-test-self@lid'), false);
    guard.release(token);
});

test('normalizeWhatsAppAddress removes suffixes and punctuation', () => {
    assert.equal(normalizeWhatsAppAddress('+1 (212) 555-0100@c.us'), '12125550100');
    assert.equal(normalizeWhatsAppAddress('12125550100@lid'), '12125550100');
    assert.equal(normalizeWhatsAppAddress(''), '');
});

test('classifyWhatsAppMessage accepts standard self-chat sends', () => {
    const classification = classifyWhatsAppMessage(
        {
            fromMe: true,
            from: '12125550100@c.us',
            to: '12125550100@c.us',
            body: 'hello',
            id: { _serialized: 'abc' },
        },
        {
            phoneNumber: '12125550100',
        },
    );

    assert.equal(classification.fromMe, true);
    assert.equal(classification.selfChat, true);
    assert.equal(classification.shouldProcess, true);
    assert.equal(classification.fingerprint, 'abc');
});

test('classifyWhatsAppMessage ignores self-chat messages when fromMe is missing', () => {
    const classification = classifyWhatsAppMessage(
        {
            fromMe: false,
            from: '12125550100@c.us',
            to: 'autoyou-test-self@lid',
            body: 'hello from notes to self',
            timestamp: 12345,
        },
        {
            phoneNumber: '12125550100',
            knownSelfChatId: 'autoyou-test-self@lid',
            chat: {
                id: {
                    _serialized: 'autoyou-test-self@lid',
                    user: 'autoyou-test-self',
                },
            },
        },
    );

    assert.equal(classification.fromMe, false);
    assert.equal(classification.selfChat, true);
    assert.equal(classification.shouldProcess, false);
});

test('classifyWhatsAppMessage accepts self-chat messages whose lid chat id does not match the phone number', () => {
    const classification = classifyWhatsAppMessage(
        {
            fromMe: true,
            from: '12125550100@c.us',
            to: 'autoyou-test-self@lid',
            body: 'hello from whatsapp notes to self',
            timestamp: 24680,
        },
        {
            phoneNumber: '12125550100',
            knownSelfChatId: 'autoyou-test-self@lid',
            chat: {
                id: {
                    _serialized: 'autoyou-test-self@lid',
                    user: 'autoyou-test-self',
                },
            },
            chatContact: {
                isMe: true,
            },
        },
    );

    assert.equal(classification.fromMe, true);
    assert.equal(classification.chatContactIsMe, true);
    assert.equal(classification.selfChat, true);
    assert.equal(classification.shouldProcess, true);
});

test('classifyWhatsAppMessage ignores sent messages to other chats', () => {
    const classification = classifyWhatsAppMessage(
        {
            fromMe: true,
            from: '12125550100@c.us',
            to: '12125550101@c.us',
            body: 'sent to someone else',
            timestamp: 67890,
        },
        {
            phoneNumber: '12125550100',
            chat: {
                id: {
                    _serialized: '12125550101@c.us',
                    user: '12125550101',
                },
            },
        },
    );

    assert.equal(classification.selfChat, false);
    assert.equal(classification.shouldProcess, false);
});

test('classifyWhatsAppMessage rejects inbound direct messages from other people even when sent to our number', () => {
    const classification = classifyWhatsAppMessage(
        {
            fromMe: false,
            from: 'external-test-contact@lid',
            to: '12125550100@c.us',
            body: 'external direct message',
            timestamp: 42424,
        },
        {
            phoneNumber: '12125550100',
            chat: {
                id: {
                    _serialized: 'external-test-contact@lid',
                    user: 'external-test-contact',
                },
            },
            chatContact: {
                isMe: false,
            },
        },
    );

    assert.equal(classification.fromMe, false);
    assert.equal(classification.toOwnAddress, true);
    assert.equal(classification.selfChat, false);
    assert.equal(classification.shouldProcess, false);
});

test('classifyWhatsAppMessage does not treat sender contact isMe alone as a self-chat signal', () => {
    const classification = classifyWhatsAppMessage(
        {
            fromMe: true,
            from: '12125550100@c.us',
            to: '12125550101@c.us',
            body: 'sent to another person',
            timestamp: 31415,
        },
        {
            phoneNumber: '12125550100',
            chat: {
                id: {
                    _serialized: '12125550101@c.us',
                    user: '12125550101',
                },
            },
            contact: {
                isMe: true,
            },
        },
    );

    assert.equal(classification.messageContactIsMe, true);
    assert.equal(classification.selfChat, false);
    assert.equal(classification.shouldProcess, false);
});

test('messageFingerprint falls back to message content when serialized id is missing', () => {
    const fingerprint = messageFingerprint({
        timestamp: 99,
        from: '12125550100@c.us',
        to: '12125550100@lid',
        body: 'hello',
    });

    assert.equal(fingerprint, '99|12125550100@c.us|12125550100@lid|hello');
});

test('isIgnorableChannelMetadataLookupError detects whatsapp-web.js channel parser bug', () => {
    const error = new TypeError("Cannot read properties of undefined (reading 'description')");
    error.stack = [
        "TypeError: Cannot read properties of undefined (reading 'description')",
        "    at Channel._patch (/app/node_modules/whatsapp-web.js/src/structures/Channel.js:44:49)",
        "    at ChatFactory.create (/app/node_modules/whatsapp-web.js/src/factories/ChatFactory.js:14:20)",
        "    at Client.getChatById (/app/node_modules/whatsapp-web.js/src/Client.js:1191:27)",
    ].join('\n');

    assert.equal(isIgnorableChannelMetadataLookupError(error), true);
});

test('isIgnorableChannelMetadataLookupError rejects unrelated description errors', () => {
    const error = new TypeError("Cannot read properties of undefined (reading 'description')");
    error.stack = "TypeError: Cannot read properties of undefined (reading 'description')\n    at application.js:12:1";

    assert.equal(isIgnorableChannelMetadataLookupError(error), false);
});

test('isIgnorableContactMetadataLookupError detects missing contact id metadata', () => {
    const memoizeError = new Error(
        "Data passed to getter must include an id property (it's how we memoize) but got undefined",
    );
    const serializedError = new TypeError("Cannot read properties of undefined (reading '_serialized')");

    assert.equal(isIgnorableContactMetadataLookupError(memoizeError), true);
    assert.equal(isIgnorableContactMetadataLookupError(serializedError), true);
});

test('isIgnorableContactMetadataLookupError rejects unrelated contact failures', () => {
    const error = new Error('synthetic contact backend failure');

    assert.equal(isIgnorableContactMetadataLookupError(error), false);
});
