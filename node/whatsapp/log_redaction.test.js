// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    messageTextSummary,
    redactIdentifier,
} from './log_redaction.js';

test('redactIdentifier preserves WhatsApp JID shape without full local identifiers', () => {
    assert.equal(redactIdentifier('12125550100@c.us'), '***0100@c.us');
    assert.equal(redactIdentifier('210835783835866@lid'), '***5866@lid');
    assert.equal(redactIdentifier('120363026970707685@g.us'), '***7685@g.us');
});

test('redactIdentifier redacts phone numbers and short identifiers', () => {
    assert.equal(redactIdentifier('+1 (212) 555-0100'), '+***0100');
    assert.equal(redactIdentifier('abc'), '***');
});

test('messageTextSummary reports length without including content', () => {
    const text = 'synthetic private WhatsApp body';
    const summary = messageTextSummary(text);

    assert.equal(summary, `len=${text.length}`);
    assert.equal(summary.includes(text), false);
});
