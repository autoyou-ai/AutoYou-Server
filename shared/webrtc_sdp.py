"""Small SDP helpers shared by AutoYou pairing paths."""

from __future__ import annotations


def mark_sdp_ice_gathering_complete(sdp: object) -> str:
    """Add per-media end-of-candidates markers for one-shot SDP exchange."""

    text = str(sdp or "")
    if not text.strip():
        return text

    separator = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()

    output: list[str] = []
    section: list[str] = []

    def flush_section() -> None:
        nonlocal section
        if section and section[0].startswith("m="):
            has_candidate = any(line.startswith("a=candidate:") for line in section)
            has_done_marker = any(line == "a=end-of-candidates" for line in section)
            if has_candidate and not has_done_marker:
                section.append("a=end-of-candidates")
        output.extend(section)
        section = []

    for line in lines:
        if line.startswith("m="):
            flush_section()
        section.append(line)

    flush_section()
    return separator.join(output) + separator
