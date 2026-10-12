// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// See LICENSE in the project root for license information.
// AI/ML training use prohibited without written authorization (License S3.9).
// AUTOYOU-PROVENANCE-R-via-de39c415bcfd73e61266c60f

import path from "node:path";
import { pathToFileURL } from "node:url";

const packageDir = String(process.env.AUTOYOU_TUNNELMOLE_NODE_PACKAGE_DIR || "").trim();
const wsEndpoint = String(process.env.AUTOYOU_TUNNELMOLE_NODE_WS_ENDPOINT || "").trim();
const httpEndpoint = String(process.env.AUTOYOU_TUNNELMOLE_NODE_HTTP_ENDPOINT || "").trim();
const apiKey = String(process.env.AUTOYOU_TUNNELMOLE_NODE_API_KEY || "").trim();
const domain = String(process.env.AUTOYOU_TUNNELMOLE_NODE_DOMAIN || "").trim();
const port = Number.parseInt(String(process.env.AUTOYOU_TUNNELMOLE_NODE_PORT || "0"), 10);
// Public mode drives the same lockfile-pinned npm client against the public
// tunnelmole service, replacing the unsigned tmole binary download. It never
// sets an API key, and runs in its own home so a self-hosted key cannot leak.
const publicMode = String(process.env.AUTOYOU_TUNNELMOLE_NODE_PUBLIC || "").trim() === "1";

const fail = (message, error) => {
    console.error(`[tunnelmole-node-launcher] ${message}`);
    if (error && typeof error === "object" && "stack" in error && error.stack) {
        console.error(String(error.stack));
    } else if (error) {
        console.error(String(error));
    }
    process.exit(1);
};

if (!packageDir || !Number.isFinite(port) || port <= 0) {
    fail("Missing required tunnelmole launcher environment.");
}
if (publicMode && (wsEndpoint || httpEndpoint || apiKey)) {
    fail("Public mode must not carry self-hosted endpoints or an API key.");
}
if (!publicMode && (!wsEndpoint || !httpEndpoint || !apiKey)) {
    fail("Missing required self-hosted tunnelmole launcher environment.");
}

const packagePath = path.resolve(packageDir);

try {
    const configModuleUrl = pathToFileURL(path.join(packagePath, "dist", "config.js")).href;
    const storageModuleUrl = pathToFileURL(path.join(packagePath, "dist", "src", "node-persist", "storage.js")).href;
    const tunnelmoleModuleUrl = pathToFileURL(path.join(packagePath, "dist", "src", "index.js")).href;

    const { default: config } = await import(configModuleUrl);
    const storageModule = await import(storageModuleUrl);
    const tunnelmoleModule = await import(tunnelmoleModuleUrl);

    if (!publicMode) {
        config.hostip.endpoint = wsEndpoint;
        config.hostip.httpEndpoint = httpEndpoint;
    }

    await storageModule.initStorage();
    if (!storageModule.storage) {
        fail("Tunnelmole storage did not initialize.");
    }
    if (publicMode) {
        storageModule.storage.removeItem?.("apiKey");
    } else {
        storageModule.storage.setItem("apiKey", apiKey);
    }

    const options = { port };
    if (domain) {
        options.domain = domain;
    }

    await tunnelmoleModule.tunnelmole(options, true);
} catch (error) {
    fail("Failed to start the self-hosted tunnelmole client.", error);
}
