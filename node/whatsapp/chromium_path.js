// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-R-304232623937526530302920-f8e13cde77be9ff9858a65e1

import fs from 'fs';
import path from 'path';


export const ALLOWED_CHROMIUM_BASENAMES = new Set([
    'chrome',
    'chromium',
    'chrome.exe',
    'chromium.exe',
    'Chromium',
    'Google Chrome',
    'Google Chrome Canary',
    'Google Chrome for Testing',
    'headless_shell',
    'chrome_headless_shell',
]);


export function allowCustomBrowserEnabled(rawValue = process.env.AUTOYOU_DEV_ALLOW_CUSTOM_BROWSER || '') {
    return ['1', 'true', 'yes', 'on'].includes(String(rawValue).trim().toLowerCase());
}


export function isAllowedChromiumExecutable(
    resolvedPath,
    { allowCustomBrowser = allowCustomBrowserEnabled(), pathModule = path } = {},
) {
    if (allowCustomBrowser) {
        return true;
    }
    try {
        const basename = pathModule.basename(resolvedPath);
        return ALLOWED_CHROMIUM_BASENAMES.has(basename);
    } catch (error) {
        return false;
    }
}


export function resolveChromiumExecutable(
    rawPath,
    {
        allowCustomBrowser = allowCustomBrowserEnabled(),
        fsModule = fs,
        pathModule = path,
        warn = console.warn,
        error = console.error,
    } = {},
) {
    const value = String(rawPath || '').trim();
    if (!value) {
        return undefined;
    }

    try {
        const candidate = pathModule.resolve(value);
        if (!fsModule.existsSync(candidate)) {
            return undefined;
        }
        const stat = fsModule.statSync(candidate);
        if (stat.isFile()) {
            if (!isAllowedChromiumExecutable(candidate, { allowCustomBrowser, pathModule })) {
                warn(
                    `[WhatsApp] Refusing to launch PUPPETEER_EXECUTABLE_PATH: unexpected executable basename "${pathModule.basename(candidate)}". ` +
                    'Set AUTOYOU_DEV_ALLOW_CUSTOM_BROWSER=1 to override for development.',
                );
                return undefined;
            }
            return candidate;
        }
        if (!stat.isDirectory()) {
            return undefined;
        }

        const directCandidates = [
            ['chrome.exe'],
            ['chrome'],
            ['chromium'],
            ['chrome-win', 'chrome.exe'],
            ['chrome-win64', 'chrome.exe'],
            ['chrome-linux', 'chrome'],
            ['chrome-mac', 'Chromium.app', 'Contents', 'MacOS', 'Chromium'],
            ['Chromium.app', 'Contents', 'MacOS', 'Chromium'],
            ['Google Chrome.app', 'Contents', 'MacOS', 'Google Chrome'],
        ];

        for (const parts of directCandidates) {
            const resolved = pathModule.join(candidate, ...parts);
            if (fsModule.existsSync(resolved) && fsModule.statSync(resolved).isFile()) {
                if (isAllowedChromiumExecutable(resolved, { allowCustomBrowser, pathModule })) {
                    return resolved;
                }
            }
        }

        const chromiumDirs = fsModule
            .readdirSync(candidate, { withFileTypes: true })
            .filter((entry) => entry.isDirectory() && entry.name.startsWith('chromium-'))
            .map((entry) => pathModule.join(candidate, entry.name))
            .sort()
            .reverse();
        for (const chromiumDir of chromiumDirs) {
            for (const parts of directCandidates) {
                const resolved = pathModule.join(chromiumDir, ...parts);
                if (fsModule.existsSync(resolved) && fsModule.statSync(resolved).isFile()) {
                    if (isAllowedChromiumExecutable(resolved, { allowCustomBrowser, pathModule })) {
                        return resolved;
                    }
                }
            }
        }
    } catch (caughtError) {
        error('[WhatsApp] Failed to resolve Chromium executable path:', caughtError);
    }

    return undefined;
}