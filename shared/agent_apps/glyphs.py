# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""The built-in web-icon glyph set for Agent Apps.

Each glyph is a 24x24 outline drawn with one stroke weight, the way a browser's
default site icon is: a plain symbol that reads at any size. The page paints
them white over a per-app gradient, from one inline SVG sprite, so a phone
needs no extra request per icon and the same markup works in every WebView.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from typing import Dict

#: Glyphs a website manifest may choose with its ``icon`` field.
APP_GLYPHS: Dict[str, str] = {
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3.2 3 3.2 15 0 18M12 3c-3.2 3-3.2 15 0 18"/>',
    "page": '<path d="M12 3 3 8l9 5 9-5-9-5Z"/><path d="m3 12.5 9 5 9-5M3 17l9 5 9-5"/>',
    "notes": '<path d="M7 3h7.5L19 7.5V19a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/><path d="M14 3v5h5M8.5 12.5h7M8.5 16.5h4.5"/>',
    "tasks": '<rect x="4" y="4" width="16" height="16" rx="4.5"/><path d="m8.4 12.4 2.5 2.5 4.8-5.6"/>',
    "bell": '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.6 2H4.4l1.6-2Z"/><path d="M10 21.2h4"/>',
    "folder": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2.4h7.5A2.5 2.5 0 0 1 21 9.9v7.6a2.5 2.5 0 0 1-2.5 2.5h-13A2.5 2.5 0 0 1 3 17.5v-10Z"/>',
    "music": '<path d="M9 18V6.2l10-2V16"/><circle cx="6.5" cy="18" r="2.6"/><circle cx="16.5" cy="16" r="2.6"/>',
    "film": '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M8 5v14M16 5v14M3 10h5M3 14h5M16 10h5M16 14h5"/>',
    "bolt": '<path d="M13.2 3 5 13.6h6.2L10.4 21 19 10.4h-6.3L13.2 3Z"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4.2 20.5c.5-4 3.6-6.3 7.8-6.3s7.3 2.3 7.8 6.3"/>',
    "sparkles": '<path d="M10 4l1.7 4.6L16.3 10l-4.6 1.7L10 16.3l-1.7-4.6L3.7 10l4.6-1.4L10 4Z"/><path d="m18 14 .9 2.1 2.1.9-2.1.9L18 20l-.9-2.1-2.1-.9 2.1-.9.9-2.1Z"/>',
    "layout": '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3 9.2h18M9 9.2V20"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7.5 9.5 3 2.5-3 2.5M13 15h4"/>',
    "gamepad": '<path d="M7.5 7h9a5 5 0 0 1 4.9 6l-.7 3.2a2.8 2.8 0 0 1-5 1l-1.2-1.7H9.5l-1.2 1.7a2.8 2.8 0 0 1-5-1L2.6 13a5 5 0 0 1 4.9-6Z"/><path d="M8 10.3v3.4M6.3 12h3.4M16 11.2h.01M18 13h.01"/>',
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.2a6.5 6.5 0 0 0 13 0M12 17.7V21M9 21h6"/>',
    "sliders": '<path d="M4 7h8.5M17 7h3M4 17h3.5M12 17h8"/><circle cx="14.8" cy="7" r="2.3"/><circle cx="9.7" cy="17" r="2.3"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2.6"/><path d="M8 20h8M12 16v4"/>',
    "archive": '<rect x="3" y="4" width="18" height="5" rx="1.6"/><path d="M5 9v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9M10 13h4"/>',
    "pin": '<path d="M12 21.2s7-5.5 7-11.2a7 7 0 1 0-14 0c0 5.7 7 11.2 7 11.2Z"/><circle cx="12" cy="10" r="2.5"/>',
    "broadcast": '<circle cx="12" cy="12" r="1.9"/><path d="M8.2 8.2a5.4 5.4 0 0 0 0 7.6M15.8 8.2a5.4 5.4 0 0 1 0 7.6M5.3 5.3a9.5 9.5 0 0 0 0 13.4M18.7 5.3a9.5 9.5 0 0 1 0 13.4"/>',
    "route": '<circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="6" r="2.5"/><path d="M8.5 18H15a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h6.5"/>',
    "settings": '<circle cx="12" cy="12" r="3.2"/><path d="M12 3v2.6M12 18.4V21M3 12h2.6M18.4 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M18.4 5.6l-1.8 1.8M7.4 16.6l-1.8 1.8"/>',
    "shield": '<path d="M12 3 5 6v5.5c0 4.3 2.9 7.9 7 9.5 4.1-1.6 7-5.2 7-9.5V6l-7-3Z"/><path d="m9 12 2.2 2.2L15.5 10"/>',
    "book": '<path d="M5 4.5A2.5 2.5 0 0 1 7.5 2H19v15H7.5A2.5 2.5 0 0 0 5 19.5v-15Z"/><path d="M5 19.5A2.5 2.5 0 0 0 7.5 22H19v-5M9 6.5h6.5M9 10h4.5"/>',
    "heart": '<path d="M12 20.3s-8-4.7-8-10.6A4.4 4.4 0 0 1 12 7.3a4.4 4.4 0 0 1 8 2.4c0 5.9-8 10.6-8 10.6Z"/>',
    "coins": '<circle cx="12" cy="12" r="9"/><path d="M14.8 9.3C14.3 8.5 13.3 8 12 8c-1.7 0-2.9.9-2.9 2.1 0 3 5.8 1.5 5.8 4.5 0 1.2-1.3 2.1-2.9 2.1-1.3 0-2.4-.5-3-1.4M12 6.2V8M12 16v1.8"/>',
    "tv": '<rect x="3" y="4.5" width="18" height="12.5" rx="2.6"/><path d="m10.5 8.2 4 2.5-4 2.5V8.2ZM8 21h8"/>',
    "database": '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    "camera": '<path d="M4 8h3l1.5-2.5h7L17 8h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1Z"/><circle cx="12" cy="13" r="3.5"/>',
    "chat": '<path d="M4 6.5A3.5 3.5 0 0 1 7.5 3h9A3.5 3.5 0 0 1 20 6.5v6a3.5 3.5 0 0 1-3.5 3.5H11l-4.5 4v-4A3.5 3.5 0 0 1 4 12.5v-6Z"/>',
    "mail": '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m4 7.5 8 6 8-6"/>',
    "calendar": '<rect x="3.5" y="5" width="17" height="15.5" rx="3"/><path d="M3.5 10h17M8 3v4M16 3v4"/>',
    "chart": '<path d="M4 4v16h16"/><path d="m8 15.5 3.5-4.5 3 2.5L20 7"/>',
    "cloud": '<path d="M7 18.5a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 17.9 9.2 4.7 4.7 0 0 1 17 18.5H7Z"/>',
    "lock": '<rect x="5" y="10.5" width="14" height="10" rx="2.6"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5M12 14.5v2.2"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="m11 12 8.5-8.5M16.5 6.5l2.5 2.5M14 9l2 2"/>',
    "code": '<path d="m8 8-5 4 5 4M16 8l5 4-5 4M14 5l-4 14"/>',
    "home": '<path d="M4 11 12 4l8 7v8.5a1.5 1.5 0 0 1-1.5 1.5H15v-6H9v6H5.5A1.5 1.5 0 0 1 4 19.5V11Z"/>',
}

#: Chrome glyphs the page itself uses; manifests cannot select these.
UI_GLYPHS: Dict[str, str] = {
    "ui-mark": '<rect x="3.5" y="3.5" width="7.5" height="7.5" rx="2.2"/><rect x="13" y="3.5" width="7.5" height="7.5" rx="2.2"/><rect x="3.5" y="13" width="7.5" height="7.5" rx="2.2"/><path d="m16.75 13.2.9 2.3 2.3.9-2.3.9-.9 2.3-.9-2.3-2.3-.9 2.3-.9.9-2.3Z"/>',
    "ui-search": '<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4.5 4.5"/>',
    "ui-refresh": '<path d="M20 11a8 8 0 1 0-2.3 6.3M20 4.5V11h-6.5"/>',
    "ui-close": '<path d="m6 6 12 12M18 6 6 18"/>',
    "ui-text": '<path d="m3 18 4.5-12L12 18M4.7 14h5.6M14.5 18l3.2-8.5L21 18M15.7 15.5h3.9"/>',
    "ui-copy": '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.6"/><path d="M15.5 8.5V6.6A2.6 2.6 0 0 0 12.9 4H6.6A2.6 2.6 0 0 0 4 6.6v6.3a2.6 2.6 0 0 0 2.6 2.6h1.9"/>',
    "ui-open": '<path d="M13 5h6v6M19 5l-8.5 8.5M17 14.5V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2h3.5"/>',
    "ui-grip": '<path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01"/>',
    "ui-up": '<path d="m6 14 6-6 6 6"/>',
    "ui-check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "ui-wifi-off": '<path d="M3 3l18 18M8.5 16.4a5 5 0 0 1 7 0M5 12.9a10 10 0 0 1 3-2M2 9.3A15 15 0 0 1 8.3 6M12 20h.01M19 12.9a10 10 0 0 0-3.2-2.1M22 9.3a15 15 0 0 0-8-3.2"/>',
}

GLYPH_NAMES = frozenset(APP_GLYPHS)


def sprite_markup() -> str:
    """One hidden SVG holding every glyph as a ``<symbol id="g-name">``."""
    symbols = "".join(
        f'<symbol id="g-{name}" viewBox="0 0 24 24">{body}</symbol>'
        for name, body in {**APP_GLYPHS, **UI_GLYPHS}.items()
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" '
        'style="position:absolute" aria-hidden="true" focusable="false">'
        f"{symbols}</svg>"
    )
