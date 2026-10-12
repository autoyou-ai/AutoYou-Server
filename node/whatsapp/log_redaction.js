// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-N-license-0fb07a647daae2bf1119440e

function redactIdentifier(value) {
    const raw = String(value ?? '').trim();
    if (!raw) {
        return '-';
    }

    if (raw.includes('@')) {
        const [local, ...suffixParts] = raw.split('@');
        const suffix = suffixParts.join('@');
        return local ? `${redactIdentifier(local)}@${suffix}` : `***@${suffix}`;
    }

    const hasPlus = raw.startsWith('+');
    const digits = raw.replace(/\D+/g, '');
    if (digits.length >= 7) {
        return `${hasPlus ? '+' : ''}***${digits.slice(-4)}`;
    }

    if (raw.length <= 4) {
        return '***';
    }
    if (raw.length <= 8) {
        return `${raw[0]}***${raw[raw.length - 1]}`;
    }
    return `${raw.slice(0, 2)}***${raw.slice(-2)}`;
}

function messageTextSummary(value) {
    return `len=${String(value ?? '').length}`;
}

export {
    messageTextSummary,
    redactIdentifier,
};
