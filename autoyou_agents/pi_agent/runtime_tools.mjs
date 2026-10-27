// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
// Only AutoYou's validated workspace tools are exposed to the Pi model.
export default async function (pi) {
    const endpoint = process.env.AUTOYOU_PI_TOOL_URL;
    const token = process.env.AUTOYOU_PI_TOOL_TOKEN;
    if (!endpoint || !token) throw new Error('AutoYou workspace bridge is unavailable');
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
    const listing = await fetch(endpoint + '/tools', { headers });
    if (!listing.ok) throw new Error('AutoYou workspace bridge rejected tool discovery');
    for (const tool of await listing.json()) {
        pi.registerTool({
            name: tool.name,
            label: tool.name,
            description: tool.description,
            parameters: tool.parameters,
            async execute(_id, params, signal) {
                const response = await fetch(endpoint + '/call/' + tool.name, {
                    method: 'POST', headers, body: JSON.stringify(params), signal,
                });
                if (!response.ok) throw new Error('AutoYou workspace tool request failed');
                const result = await response.json();
                return {
                    content: [{ type: 'text', text: JSON.stringify(result) }],
                    details: result,
                    isError: result.status === 'error',
                };
            },
        });
    }
}
