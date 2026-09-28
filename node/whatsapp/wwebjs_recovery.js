// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.

/**
 * Keep whatsapp-web.js page reinjection from racing itself during navigation.
 * The library invokes authStrategy.logout() for automatic post_logout recovery;
 * AutoYou deletes the profile only through its explicit reset flow.
 */
export function installWWebJsRecoveryGuards(client, {
    isRecoverablePageError = () => false,
    onRecoverableNavigationError = () => {},
    onAutomaticLogout = () => {},
} = {}) {
    if (!client || typeof client.inject !== 'function') {
        return () => {};
    }

    const originalInject = client.inject;
    const originalInjectBound = originalInject.bind(client);
    const authStrategy = client.authStrategy;
    const originalLogout = authStrategy && authStrategy.logout;
    let clientReady = false;
    let inFlightInjection = null;

    const onReady = () => {
        clientReady = true;
    };

    const guardedInject = (...args) => {
        if (inFlightInjection) {
            return inFlightInjection;
        }

        const operation = (async () => {
            try {
                return await originalInjectBound(...args);
            } catch (error) {
                if (!clientReady || !isRecoverablePageError(error)) {
                    throw error;
                }
                onRecoverableNavigationError(error);
                return null;
            }
        })();
        inFlightInjection = operation;
        operation.then(() => {
            if (inFlightInjection === operation) {
                inFlightInjection = null;
            }
        }, () => {
            if (inFlightInjection === operation) {
                inFlightInjection = null;
            }
        });
        return operation;
    };

    client.inject = guardedInject;
    client.once('ready', onReady);

    if (typeof originalLogout === 'function') {
        authStrategy.logout = async () => {
            onAutomaticLogout();
        };
    }

    return () => {
        client.inject = originalInject;
        if (typeof originalLogout === 'function') {
            authStrategy.logout = originalLogout;
        }
    };
}
