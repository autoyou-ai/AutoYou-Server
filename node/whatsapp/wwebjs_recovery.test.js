// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-K-2d2030786345634238313737-fb2ec5f9d75c5618d02a6478

import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { installWWebJsRecoveryGuards } from './wwebjs_recovery.js';

test('serializes recoverable reinjection and preserves LocalAuth', async () => {
    const client = new EventEmitter();
    let injectCalls = 0;
    let logoutCalls = 0;
    let recoveredErrors = 0;
    client.authStrategy = {
        logout: async () => {
            logoutCalls++;
        },
    };
    client.inject = async () => {
        injectCalls++;
        await new Promise((resolve) => setTimeout(resolve, 1));
        throw new Error('Execution context was destroyed, most likely because of a navigation.');
    };

    const restore = installWWebJsRecoveryGuards(client, {
        isRecoverablePageError: (error) => /Execution context was destroyed/.test(error.message),
        onRecoverableNavigationError: () => {
            recoveredErrors++;
        },
    });
    client.emit('ready');

    assert.deepEqual(await Promise.all([client.inject(), client.inject()]), [null, null]);
    await client.authStrategy.logout();
    assert.equal(injectCalls, 1);
    assert.equal(recoveredErrors, 1);
    assert.equal(logoutCalls, 0);
    restore();
});

test('does not hide startup injection errors', async () => {
    const client = new EventEmitter();
    client.authStrategy = { logout: async () => {} };
    client.inject = async () => {
        throw new Error('auth timeout');
    };
    installWWebJsRecoveryGuards(client, {
        isRecoverablePageError: () => true,
    });

    await assert.rejects(client.inject(), /auth timeout/);
});
