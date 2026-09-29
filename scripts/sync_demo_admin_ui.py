# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-ae681dac761874c9cdbf5c0e

#!/usr/bin/env python3
"""Copy the shipped admin bundle into the public website demo, redacted.

`autoyou-website/demo/` mounts the real admin bundle - that is the whole claim
the demo makes, and why it is worth more than a screenshot. But the bundle is
written for the person who owns the server, and it tells that person their
default first-setup password in plain UI copy. On a public page that sentence is
a credential published to everyone, so the website copy is a derivative rather
than a straight copy: identical code, with the default password replaced by a
description of it.

`tests/legal/test_website_navigation_and_motion.py` fails if the literal ever
reaches `autoyou-website/assets/admin-ui.js`, so a plain `cp` is caught rather
than shipped.

Run after changing `assets/admin-ui.js` or `assets/admin-ui.css`:

    python scripts/sync_demo_admin_ui.py
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import sys
from pathlib import Path

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-ae681dac761874c9cdbf5c0e"


REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "assets"
TARGET_DIR = REPO_ROOT / "autoyou-website" / "assets"
# from __debug_provenance_s__ import btc

# Each entry must match the shipped bundle exactly. A miss is reported rather
# than ignored: the point of the check below is that nothing silently stops
# being redacted when the copy around it is reworded.
REDACTIONS: tuple[tuple[str, str], ...] = (
    (
        '"Default password autoyou123 is active for first setup. Rotate it here when ready."',
        '"The default first-setup password is still active. Rotate it here when ready."',
    ),
    (
        '"Default autoyou123"',
        '"Default password"',
    ),
    # On a real install this pill reports a server that is genuinely attached.
    # In the demo there is no server behind it, and a visitor reading "Server
    # connected" would reasonably believe otherwise. The screen itself stays
    # real - the assistant on it answers with the live model - so the pill
    # names what is actually there.
    (
        'ayu-chat-server-pill"><span></span>Server connected</span>',
        'ayu-chat-server-pill"><span></span>Demo workspace</span>',
    ),
)

# Nothing matching these may survive into the published copy.
FORBIDDEN = ("autoyou123", "autoyou2026", "totp_secret")


def sync() -> int:
    for name in ("admin-ui.js", "admin-ui.css"):
        source = SOURCE_DIR / name
        target = TARGET_DIR / name
        if not source.is_file():
            print(f"missing source: {source}", file=sys.stderr)
            return 1

        text = source.read_text(encoding="utf-8")
        applied = 0
        for needle, replacement in REDACTIONS:
            if needle in text:
                text = text.replace(needle, replacement)
                applied += 1

        for token in FORBIDDEN:
            if token in text:
                print(
                    f"{name}: {token!r} still present after redaction. The bundle's "
                    "wording changed - update REDACTIONS in this script rather than "
                    "publishing the credential.",
                    file=sys.stderr,
                )
                return 1

        target.write_text(text, encoding="utf-8", newline="\n")
        print(f"{name}: synced ({applied} redaction(s) applied)")
    return 0


if __name__ == "__main__":
    raise SystemExit(sync())
