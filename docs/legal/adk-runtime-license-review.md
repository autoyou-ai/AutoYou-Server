# ADK runtime license and notice review

Reviewed on 2026-10-10 for ADK 2.11.0 and the AutoYou MCP SDK 2.3.0 update.
The requested review used `gpt-6-astra` to inspect source and primary upstream
license materials. This records an engineering license assessment, not a
certification of every release artifact or the applicability of service terms.

## Optional ADK Web controls

[Apache-2.0](https://www.apache.org/licenses/LICENSE-2.0) permits modified
redistribution subject to its conditions. It does not require an optional
Usage Metrics toggle or telemetry consent modal to remain available. AutoYou
patches private writable or staged assets, retains the development disclosure,
and places prominent modification notices in changed JavaScript and HTML.
Original license and attribution materials remain applicable. The UI change
does not establish that CLI telemetry, exporters, or provider logging is disabled.

The [ADK Web README](https://github.com/google/adk-web/blob/main/README.md)
also references Preview and [Google Cloud Pre-GA terms](https://cloud.google.com/terms/service-terms).
An Apache permission assessment does not override service/account terms or
make this development UI suitable for production.

## Corrected distribution gaps

The review found that the embedded frontend lacked its extracted dependency
notices. All 113 browser files in the [ADK Web v1.0.7 archive](https://github.com/google/adk-web/releases/download/v1.0.7/adk-web-browser.zip)
matched the ADK 2.11.0 wheel by SHA256. The release identifies source commit
`34f3f8d7946c76faa664658390dcccf1e4e34677`. Its lockfile is the source for
`docs/legal/licenses/google-adk-web-notice-provenance.json` and the conservative
211-entry dependency notice collection. It can include unused packages and is
separate from a binary SBOM.

Exact published package notices are retained, including Angular, RxJS,
Font Awesome, A2UI, and DOMPurify. Where a reviewed package supplies only a
license declaration, original attribution is retained and reproduced standard
MIT/ISC terms are explicitly labeled. No copyright years are invented.

The embedded Viz.js runtime also includes Graphviz 15.1.1, Expat 2.8.1,
Emscripten 5.0.5, and musl. Their notices are included. Graphviz's EPL-2.0
source availability statement identifies the [exact Graphviz source](https://gitlab.com/api/v4/projects/4207231/packages/generic/graphviz-releases/15.1.1/graphviz-15.1.1.tar.gz)
and [Viz.js backend build sources](https://github.com/mdaines/viz-js/tree/96be4ed32789125f00cffa3ae390bd8809fc9c03/packages/viz/backend).
AutoYou does not modify those native sources. Recipients retain their
third-party license rights independently of AutoYou's license.

The final v2 Windows MSIX packager and archive verifier now require the Full
profile's ADK, frontend, MCP, and HTTP dependency notices. Server Windows/macOS
packaging and v2 macOS/Windows staging carry the same materials. This closes
the older-distribution path that could bypass the publisher's checks.

## Future upgrades

Review changed licenses, notices, embedded dependency provenance, and UI
behavior before changing the dependency pin. The sanitizer uses semantic
anchors, fails on recognized unsupported layouts, and requires manual review
when a new release removes all known markers. No universal compatibility or
complete future license-compliance guarantee is made.
