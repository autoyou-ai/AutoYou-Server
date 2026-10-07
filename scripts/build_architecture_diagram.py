#!/usr/bin/env python3
"""Draw the README architecture diagram as light and dark SVG files.

    python scripts/build_architecture_diagram.py docs/images

The README embeds the PNGs because GitHub mobile and some viewers substitute fonts in SVG. Render them
from the SVGs with headless Chrome at 2x:

    chrome --headless=new --hide-scrollbars --force-device-scale-factor=2 --window-size=1280,590 \\
        --screenshot=docs/images/architecture-light.png file://$PWD/docs/images/architecture-light.svg

Change a label or a box here, regenerate both themes, and keep the claims true to the source.
"""
import sys
from pathlib import Path
from xml.sax.saxutils import escape

W, H = 1280, 590
FONT = "Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

PALETTES = {
    "light": dict(
        bg="#ffffff", panel="#f6f8fb", panel_stroke="#dbe3ee", card="#ffffff", card_stroke="#d3dce8",
        ink="#0f1b2d", muted="#52637a", shadow="rgba(15,27,45,0.10)",
        blue="#2b6cf6", blue_bg="#eaf1ff", green="#12805c", green_bg="#e6f6ef",
        teal="#0aa5a0", teal_ink="#056f6b", orange="#c26a00", orange_bg="#fff3e0",
        gray_bg="#eef1f5", gray_stroke="#9aa7b8", router="#12805c", router_ink="#ffffff",
    ),
    "dark": dict(
        bg="#0d1117", panel="#111823", panel_stroke="#26324a", card="#161e2c", card_stroke="#2c3a55",
        ink="#eaf1fb", muted="#9db0c9", shadow="rgba(0,0,0,0.45)",
        blue="#6a9bff", blue_bg="#16264a", green="#3fcf9a", green_bg="#12301f",
        teal="#22d3c5", teal_ink="#7ff0e6", orange="#f0a030", orange_bg="#3a2a0c",
        gray_bg="#1d2533", gray_stroke="#6c7a90", router="#1f8f68", router_ink="#ffffff",
    ),
}


def build(theme: str) -> str:
    c = PALETTES[theme]
    out = []
    add = out.append

    def text(x, y, s, size=14, weight=400, fill=None, anchor="start", extra=""):
        add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill or c["ink"]}" '
            f'text-anchor="{anchor}" {extra}>{escape(s)}</text>')

    def card(x, y, w, h, title, lines=(), accent=None, dashed=False, fill=None, title_size=16):
        stroke = accent or c["card_stroke"]
        dash = ' stroke-dasharray="6 4"' if dashed else ""
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill or c["card"]}" '
            f'stroke="{stroke}" stroke-width="{1.6 if accent else 1.2}"{dash} filter="url(#shadow)"/>')
        ty = y + 28
        text(x + 18, ty, title, title_size, 700, accent if dashed else None)
        for i, line in enumerate(lines):
            text(x + 18, ty + 22 + i * 18, line, 13, 400, c["muted"])

    def chip(x, y, w, h, title, sub, accent, bg, dashed=False):
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="11" fill="{bg}" stroke="{accent}" stroke-width="1.3"{dash}/>')
        text(x + 14, y + 24, title, 14.5, 650, c["ink"])
        if sub:
            if isinstance(sub, str):
                sub = [sub]
            for i, s in enumerate(sub):
                text(x + 14, y + 43 + i * 17, s, 12.5, 400, c["muted"])

    def badge(x, y, w, label):
        add(f'<rect x="{x}" y="{y}" width="{w}" height="24" rx="12" fill="{c["orange_bg"]}" stroke="{c["orange"]}" '
            f'stroke-width="1.2" stroke-dasharray="4 3"/>')
        text(x + w / 2, y + 16.5, label, 12, 600, c["orange"], "middle")

    add(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        f'font-family="{FONT}" role="img" aria-labelledby="t d">')
    add('<title id="t">AutoYou architecture</title>')
    add('<desc id="d">Your phone, laptop and browser connect to the AutoYou Server on your own computer over an '
        'end-to-end encrypted WebRTC link. Telegram, Signal or WhatsApp carry the encrypted Auto-Pair message. '
        'The server runs a Google ADK agent harness with AutoYou agents, OpenClaw and Hermes Agent, with Agent '
        'Apps, local voice, memory and models through LiteLLM to Ollama or optional cloud providers.</desc>')
    add('<defs>'
        f'<filter id="shadow" x="-10%" y="-10%" width="120%" height="130%"><feDropShadow dx="0" dy="3" stdDeviation="5" flood-color="{c["shadow"]}"/></filter>'
        f'<marker id="arr" viewBox="0 0 10 10" refX="7" refY="5" markerUnits="userSpaceOnUse" markerWidth="15" markerHeight="15" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{c["teal"]}"/></marker>'
        f'<marker id="arrg" viewBox="0 0 10 10" refX="7" refY="5" markerUnits="userSpaceOnUse" markerWidth="11" markerHeight="11" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{c["gray_stroke"]}"/></marker>'
        f'<marker id="arrd" viewBox="0 0 10 10" refX="7" refY="5" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{c["muted"]}"/></marker>'
        '</defs>')
    add(f'<rect width="{W}" height="{H}" fill="{c["bg"]}"/>')

    # containers
    add(f'<rect x="24" y="24" width="360" height="504" rx="22" fill="{c["panel"]}" stroke="{c["panel_stroke"]}" stroke-width="1.2"/>')
    text(48, 56, "YOUR DEVICES", 12.5, 700, c["blue"], extra='letter-spacing="1.6"')
    add(f'<rect x="704" y="24" width="552" height="504" rx="22" fill="{c["panel"]}" stroke="{c["panel_stroke"]}" stroke-width="1.2"/>')
    text(728, 56, "AUTOYOU SERVER  ·  YOUR COMPUTER", 12.5, 700, c["green"], extra='letter-spacing="1.6"')

    # left: apps card with two inner chips
    add(f'<rect x="48" y="84" width="312" height="306" rx="16" fill="{c["card"]}" stroke="{c["blue"]}" stroke-width="1.6" filter="url(#shadow)"/>')
    text(66, 114, "AutoYou apps", 17, 750)
    text(66, 135, "iPhone · Android · Mac · Windows · Chrome", 13, 400, c["muted"])
    text(66, 153, "Chat · voice · video · remote desktop", 13, 400, c["muted"])
    chip(64, 176, 280, 84, "On-device AI", ["LFM2.5 350M", "Apple Intelligence · Gemini Nano"], c["blue"], c["blue_bg"])
    chip(64, 274, 280, 84, "Local web server", ["Shows your Agent Apps", "inside the phone app"], c["blue"], c["blue_bg"])

    # left: MCP clients
    card(48, 414, 312, 84, "MCP clients", ["Cursor · Claude"], c["gray_stroke"])

    # middle connectors
    # WebRTC (thick)
    add(f'<line x1="364" y1="170" x2="720" y2="170" stroke="{c["teal"]}" stroke-width="5" stroke-linecap="round" marker-start="url(#arr)" marker-end="url(#arr)"/>')
    text(542, 140, "End-to-end encrypted WebRTC", 15, 750, c["teal_ink"], "middle")
    text(542, 197, "chat · voice · video", 13, 500, c["muted"], "middle")
    text(542, 215, "Agent Apps tunnel (HTTP over SCTP)", 13, 500, c["muted"], "middle")
    # Auto-Pair via messaging
    add(f'<line x1="364" y1="310" x2="720" y2="310" stroke="{c["gray_stroke"]}" stroke-width="2" stroke-linecap="round" marker-start="url(#arrg)" marker-end="url(#arrg)"/>')
    text(542, 274, "Auto-Pair (encrypted message)", 14, 700, c["ink"], "middle")
    add(f'<rect x="422" y="290" width="240" height="40" rx="20" fill="{c["gray_bg"]}" stroke="{c["gray_stroke"]}" stroke-width="1.3" stroke-dasharray="5 4"/>')
    text(542, 315, "Telegram · Signal · WhatsApp", 13, 600, c["ink"], "middle")
    # MCP bridge
    add(f'<line x1="364" y1="456" x2="720" y2="456" stroke="{c["gray_stroke"]}" stroke-width="2" stroke-linecap="round" marker-end="url(#arrg)"/>')
    text(542, 444, "MCP bridge", 13, 600, c["muted"], "middle")

    # right: router bar
    add(f'<rect x="724" y="84" width="64" height="424" rx="16" fill="{c["router"]}" filter="url(#shadow)"/>')
    add(f'<text transform="translate(762 296) rotate(-90)" font-size="17" font-weight="750" fill="{c["router_ink"]}" text-anchor="middle" letter-spacing="1.2">Intent router</text>')

    # harness
    add(f'<rect x="808" y="84" width="428" height="200" rx="16" fill="{c["card"]}" stroke="{c["green"]}" stroke-width="1.6" filter="url(#shadow)"/>')
    text(828, 114, "Agent harness · Google ADK", 17, 750)
    chip(828, 132, 388, 56, "AutoYou agents", "Notes · Page · Browser · Files · Coding · and more", c["green"], c["green_bg"])
    chip(828, 202, 186, 62, "OpenClaw", "bridged agent", c["orange"], c["orange_bg"], dashed=True)
    chip(1030, 202, 186, 62, "Hermes Agent", "bridged agent", c["orange"], c["orange_bg"], dashed=True)

    # services 2x2
    card(808, 304, 206, 90, "Agent Apps", ["Web apps your agents build"], c["green"])
    card(1030, 304, 206, 90, "Local voice", ["faster-whisper · system TTS"], c["green"])
    badge(1046, 362, 168, "+ EmotiVoice (optional)")
    card(808, 410, 206, 98, "Memory", ["SQLite by default"], c["green"])
    badge(824, 474, 150, "+ Cognee (optional)")
    card(1030, 410, 206, 98, "Models · LiteLLM", [], c["green"])
    badge(1046, 450, 120, "Ollama (local)")
    badge(1046, 478, 150, "Cloud (optional)")
    # server internal arrows
    add(f'<line x1="790" y1="184" x2="806" y2="184" stroke="{c["muted"]}" stroke-width="2" marker-end="url(#arrd)"/>')
    add(f'<line x1="1022" y1="286" x2="1022" y2="302" stroke="{c["muted"]}" stroke-width="2" marker-end="url(#arrd)"/>')

    # legend
    add(f'<rect x="48" y="554" width="22" height="12" rx="6" fill="{c["orange_bg"]}" stroke="{c["orange"]}" stroke-width="1.2" stroke-dasharray="4 3"/>')
    text(80, 564.5, "Optional add-ons and bridged projects you can turn on or leave out", 12.5, 400, c["muted"])
    add('</svg>')
    return "\n".join(out)


def main():
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    for theme in ("light", "dark"):
        (out_dir / f"architecture-{theme}.svg").write_text(build(theme), encoding="utf-8")
    print("wrote", [p.name for p in sorted(out_dir.glob("architecture-*.svg"))])


if __name__ == "__main__":
    main()
