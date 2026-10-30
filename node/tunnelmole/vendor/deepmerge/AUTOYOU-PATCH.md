Based on deepmerge 4.3.1 (MIT), published by TehShrike/deepmerge.
The local version 4.3.2-autoyou.1 is an AutoYou patch, not an upstream release.

GHSA-gcqx-w48m-w2h4: mergeObject() copied an own `__proto__` key (as created
by `JSON.parse`) onto the merged object, replacing its prototype. This patch
drops own `__proto__` keys when enumerating target and source objects. All
other merge behavior is unchanged.

Tunnelmole only merges its own bundled config objects, so the advisory was not
reachable from AutoYou, but no upstream release fixes it. Keep the upstream
license. Replace this local patch with a verified upstream fix when one is
published; keep the regression check in security.test.js.
