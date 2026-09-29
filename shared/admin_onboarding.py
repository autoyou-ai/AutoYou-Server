# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-c52a9a0692a46bf990745abc

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import ast
import html
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import parse_qs, urlsplit, urlunsplit

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-c52a9a0692a46bf990745abc"


DEFAULT_STUN_SERVERS = [
    {"urls": ["stun:stun.l.google.com:19302"]},
    {"urls": ["stun:stun1.l.google.com:19302"]},
]

_URL_PATTERN = re.compile(r"(?P<url>(?:stun|stuns|turn|turns):[^\s'\",]+)", re.IGNORECASE)
_ASSIGNMENT_PATTERN = re.compile(r"^\s*(?:const|let|var|export\s+const)?\s*([A-Za-z_][\w]*)\s*=\s*(.+?)\s*;?\s*$", re.DOTALL)


def _strip_code_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        lines = cleaned.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return cleaned


def _balanced_segment(text: str, start_index: int) -> Optional[str]:
    if start_index < 0 or start_index >= len(text):
        return None

    opening = text[start_index]
    if opening not in "[{":
        return None
    closing = "]" if opening == "[" else "}"

    depth = 0
    in_string = False
    string_char = ""
    escaped = False
    for index in range(start_index, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == string_char:
                in_string = False
            continue

        if char in {'"', "'"}:
            in_string = True
            string_char = char
            continue
        if char == opening:
            depth += 1
            continue
        if char == closing:
            depth -= 1
            if depth == 0:
                return text[start_index : index + 1]
    return None


def _extract_assignment_value(text: str) -> Optional[str]:
    match = _ASSIGNMENT_PATTERN.match(text.strip())
    if not match:
        return None
    return match.group(2).strip()


def _extract_ice_servers_segment(text: str) -> Optional[str]:
    match = re.search(r"iceServers\s*:", text, flags=re.IGNORECASE)
    if not match:
        return None
    bracket_index = text.find("[", match.end())
    if bracket_index == -1:
        return None
    return _balanced_segment(text, bracket_index)


def _normalize_js_like_payload(raw_text: str) -> str:
    normalized = (raw_text or "").strip()
    normalized = normalized.replace("\r\n", "\n")
    normalized = re.sub(r"^\s*new\s+RTCPeerConnection\s*\(", "", normalized)
    normalized = re.sub(r"\)\s*;?\s*$", "", normalized)
    normalized = re.sub(r"\btrue\b", "true", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bfalse\b", "false", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\bnull\b", "null", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"([{,]\s*)([A-Za-z_][\w-]*)(\s*:)", r'\1"\2"\3', normalized)
    normalized = re.sub(r",(\s*[}\]])", r"\1", normalized)
    if "'" in normalized and '"' not in normalized:
        normalized = normalized.replace("'", '"')
    return normalized


def _try_load_structured_payload(candidate: str) -> Optional[Any]:
    raw = (candidate or "").strip()
    if not raw:
        return None

    attempts = [raw]
    normalized = _normalize_js_like_payload(raw)
    if normalized != raw:
        attempts.append(normalized)

    for item in attempts:
        try:
            return json.loads(item)
        except Exception:
            pass
        try:
            return ast.literal_eval(item)
        except Exception:
            pass
    return None


def normalize_ice_server(server: Dict[str, Any]) -> Dict[str, Any]:
    urls_value = server.get("urls", server.get("url"))
    if isinstance(urls_value, str):
        urls = [urls_value.strip()]
    elif isinstance(urls_value, (list, tuple)):
        urls = [str(url).strip() for url in urls_value if str(url).strip()]
    else:
        urls = []
    if not urls:
        raise ValueError("Each connection helper must provide at least one URL")

    normalized: Dict[str, Any] = {"urls": urls}
    username = str(server.get("username", "") or "").strip()
    credential = str(server.get("credential", server.get("password", "")) or "").strip()
    if username:
        normalized["username"] = username
    if credential:
        normalized["credential"] = credential
    return normalized


def dedupe_ice_servers(servers: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for server in servers:
        try:
            normalized = normalize_ice_server(server)
        except Exception:
            continue
        key = (
            tuple(normalized.get("urls", [])),
            normalized.get("username", ""),
            normalized.get("credential", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return deduped


def _extract_global_turn_credentials(text: str) -> Tuple[str, str]:
    username_match = re.search(
        r"(?:^|\b)(?:turn_)?(?:user(?:name)?)\s*[:=]\s*['\"]?([^'\"\s,]+)",
        text,
        flags=re.IGNORECASE,
    )
    credential_match = re.search(
        r"(?:^|\b)(?:turn_)?(?:credential|pass(?:word)?)\s*[:=]\s*['\"]?([^'\"\s,]+)",
        text,
        flags=re.IGNORECASE,
    )
    username = username_match.group(1).strip() if username_match else ""
    credential = credential_match.group(1).strip() if credential_match else ""
    return username, credential


def _build_url_server(url_value: str, default_username: str = "", default_credential: str = "") -> Dict[str, Any]:
    url = (url_value or "").strip().rstrip(",")
    if not url:
        raise ValueError("Connection helper URL cannot be empty")
    server: Dict[str, Any] = {"urls": [url]}
    split = urlsplit(url)
    is_turn = split.scheme.lower().startswith("turn")
    query = parse_qs(split.query, keep_blank_values=False)
    # from __debug_provenance_m__ import of
    username = (query.get("username") or query.get("user") or [default_username if is_turn else ""])[0] or (default_username if is_turn else "")
    credential = (query.get("credential") or query.get("password") or [default_credential if is_turn else ""])[0] or (default_credential if is_turn else "")
    if query:
        query.pop("username", None)
        query.pop("user", None)
        query.pop("password", None)
        query.pop("credential", None)
        clean_query = "&".join(
            f"{key}={value}"
            for key, values in query.items()
            for value in values
        )
        clean_url = urlunsplit((split.scheme, split.netloc, split.path, clean_query, split.fragment))
        server["urls"] = [clean_url]
    if username:
        server["username"] = username
    if credential:
        server["credential"] = credential
    return server


def parse_ice_servers_input(raw_text: str) -> Dict[str, Any]:
    text = _strip_code_fences(raw_text)
    if not text.strip():
        raise ValueError("Please paste provider details, JSON, or connection server URLs.")

    structured_candidates = [text]
    assignment_value = _extract_assignment_value(text)
    if assignment_value:
        structured_candidates.append(assignment_value)

    ice_segment = _extract_ice_servers_segment(text)
    if ice_segment:
        structured_candidates.append(ice_segment)

    for candidate in structured_candidates:
        payload = _try_load_structured_payload(candidate)
        if payload is None:
            continue

        if isinstance(payload, dict) and "iceServers" in payload:
            servers = dedupe_ice_servers(payload.get("iceServers", []))
            if servers:
                return {"servers": servers, "warnings": [], "detected_source": "connection helper object"}

        if isinstance(payload, list):
            servers = dedupe_ice_servers(payload)
            if servers:
                return {"servers": servers, "warnings": [], "detected_source": "array payload"}

        if isinstance(payload, dict) and any(key in payload for key in ("urls", "url")):
            return {"servers": [normalize_ice_server(payload)], "warnings": [], "detected_source": "single server object"}

    warnings: List[str] = []
    default_username, default_credential = _extract_global_turn_credentials(text)
    servers: List[Dict[str, Any]] = []
    for match in _URL_PATTERN.finditer(text):
        try:
            servers.append(_build_url_server(match.group("url"), default_username, default_credential))
        except Exception as exc:
            warnings.append(str(exc))
    deduped = dedupe_ice_servers(servers)
    if deduped:
        return {"servers": deduped, "warnings": warnings, "detected_source": "url list"}

    raise ValueError("Could not detect any connection helpers in the provided text.")


def _env_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_text(env_values: Mapping[str, str], name: str) -> str:
    return str(env_values.get(name, "") or "").strip()


def _split_host_port(value: str, fallback_port: str) -> tuple[str, str]:
    host = value.strip()
    if host.startswith("[") and "]:" in host:
        bracket, _, raw_port = host.rpartition(":")
        if raw_port.isdigit():
            return bracket, raw_port
    if host.count(":") == 1:
        raw_host, raw_port = host.rsplit(":", 1)
        if raw_host and raw_port.isdigit():
            return raw_host, raw_port
    return host, fallback_port or "3478"


def _stun_url_for_host(host: str, port: str) -> str:
    normalized = host.strip()
    if normalized.lower().startswith(("stun:", "stuns:")):
        return normalized
    resolved_host, resolved_port = _split_host_port(normalized, port)
    return f"stun:{resolved_host}:{resolved_port}"


def _turn_url_for_host(host: str, port: str) -> str:
    normalized = host.strip()
    if normalized.lower().startswith(("turn:", "turns:")):
        return normalized
    resolved_host, resolved_port = _split_host_port(normalized, port)
    return f"turn:{resolved_host}:{resolved_port}"


def _local_stunturn_snippet(env_values: Mapping[str, str]) -> str:
    lines: List[str] = []
    common_host = _env_text(env_values, "AUTOYOU_LOCAL_STUNTURN_HOST")
    stun_host = _env_text(env_values, "AUTOYOU_LOCAL_STUN_HOST") or common_host
    turn_host = _env_text(env_values, "AUTOYOU_LOCAL_TURN_HOST") or common_host
    stun_url = _env_text(env_values, "AUTOYOU_LOCAL_STUN_URL")
    turn_url = _env_text(env_values, "AUTOYOU_LOCAL_TURN_URL")
    stun_port = _env_text(env_values, "AUTOYOU_LOCAL_STUN_PORT") or "3478"
    turn_port = _env_text(env_values, "AUTOYOU_LOCAL_TURN_PORT") or "3478"

    if stun_url:
        lines.append(stun_url)
    elif stun_host:
        lines.append(_stun_url_for_host(stun_host, stun_port))

    turn_username = _env_text(env_values, "AUTOYOU_LOCAL_TURN_USERNAME")
    turn_password = _env_text(env_values, "AUTOYOU_LOCAL_TURN_PASSWORD")
    if turn_username:
        lines.append(f"TURN_USERNAME={turn_username}")
    if turn_password:
        lines.append(f"TURN_PASSWORD={turn_password}")
    if turn_url:
        lines.append(f"TURN_URL={turn_url}")
    elif turn_host and turn_username and turn_password:
        lines.append(f"TURN_URL={_turn_url_for_host(turn_host, turn_port)}")

    return "\n".join(lines)


def load_default_ice_servers_from_env(
    env_values: Optional[Mapping[str, str]] = None,
    *,
    fallback: Optional[Iterable[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Return startup ICE defaults with private/local overrides applied.

    Production keeps the public Google STUN defaults. Operators who need Local
    Pair to stay on their own network can set one of:

    - AUTOYOU_DEFAULT_ICE_SERVERS / AUTOYOU_ICE_SERVERS / AUTOYOU_RTC_ICE_SERVERS
      with any parser-supported JSON/snippet/raw URL bundle.
    - AUTOYOU_LOCAL_STUNTURN_HOST=192.0.2.10 to build a LAN STUN URL, plus
      AUTOYOU_LOCAL_TURN_USERNAME/PASSWORD to add TURN.
    - AUTOYOU_DISABLE_PUBLIC_STUN=1 to return no public STUN fallback.
    """
    env = os.environ if env_values is None else env_values
    explicit = (
        _env_text(env, "AUTOYOU_DEFAULT_ICE_SERVERS")
        or _env_text(env, "AUTOYOU_ICE_SERVERS")
        or _env_text(env, "AUTOYOU_RTC_ICE_SERVERS")
    )
    if explicit:
        try:
            return list(parse_ice_servers_input(explicit)["servers"])
        except Exception:
            return []

    local_snippet = _local_stunturn_snippet(env)
    if local_snippet:
        try:
            return list(parse_ice_servers_input(local_snippet)["servers"])
        except Exception:
            return []

    if _env_truthy(env.get("AUTOYOU_DISABLE_PUBLIC_STUN")):
        return []

    return dedupe_ice_servers(list(fallback if fallback is not None else DEFAULT_STUN_SERVERS))


def merge_rtc_config(existing_config: Optional[Dict[str, Any]], parsed_servers: List[Dict[str, Any]], *, replace: bool = False) -> Dict[str, Any]:
    base = existing_config or {"iceServers": []}
    existing_servers = base.get("iceServers", [])
    if not isinstance(existing_servers, list):
        existing_servers = []

    if replace:
        merged = dedupe_ice_servers(parsed_servers)
    else:
        merged = dedupe_ice_servers([*existing_servers, *parsed_servers])

    return {"iceServers": merged}


def simple_markdown_to_html(markdown_text: str) -> str:
    text = (markdown_text or "").replace("\r\n", "\n").strip("\n")
    if not text:
        return ""

    blocks: List[str] = []
    lines = text.split("\n")
    in_code_block = False
    code_lines: List[str] = []
    list_items: List[str] = []
    ordered_items: List[str] = []
    paragraph_lines: List[str] = []

    def flush_paragraph() -> None:
        if paragraph_lines:
            paragraph = " ".join(line.strip() for line in paragraph_lines if line.strip())
            if paragraph:
                blocks.append(f"<p>{html.escape(paragraph)}</p>")
            paragraph_lines.clear()

    def flush_lists() -> None:
        if list_items:
            blocks.append("<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in list_items) + "</ul>")
            list_items.clear()
        if ordered_items:
            blocks.append("<ol>" + "".join(f"<li>{html.escape(item)}</li>" for item in ordered_items) + "</ol>")
            ordered_items.clear()

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            flush_lists()
            if in_code_block:
                blocks.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines.clear()
                in_code_block = False
            else:
                in_code_block = True
            continue
        if in_code_block:
            code_lines.append(line)
            continue
        if not stripped:
            flush_paragraph()
            flush_lists()
            continue
        if stripped.startswith("# "):
            flush_paragraph()
            flush_lists()
            blocks.append(f"<h1>{html.escape(stripped[2:].strip())}</h1>")
            continue
        if stripped.startswith("## "):
            flush_paragraph()
            flush_lists()
            blocks.append(f"<h2>{html.escape(stripped[3:].strip())}</h2>")
            continue
        if stripped.startswith("### "):
            flush_paragraph()
            flush_lists()
            blocks.append(f"<h3>{html.escape(stripped[4:].strip())}</h3>")
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            ordered_items.clear()
            list_items.append(stripped[2:].strip())
            continue
        if re.match(r"^\d+\.\s+", stripped):
            flush_paragraph()
            list_items.clear()
            ordered_items.append(re.sub(r"^\d+\.\s+", "", stripped).strip())
            continue
        paragraph_lines.append(stripped)

    flush_paragraph()
    flush_lists()
    if in_code_block and code_lines:
        blocks.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")

    return "\n".join(blocks)


def load_markdown_guide(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build_ollama_install_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Install Ollama First</h1>
      <p>AutoYou uses your local Ollama runtime by default. That keeps inference on your machine and avoids requiring a cloud provider for the first-run setup.</p>
      <div class='guide-callout'>
        <strong>Recommended starter model:</strong> <code>ministral-3:8b</code>. It is the default model AutoYou expects during first-run onboarding.
      </div>
      <h2>Windows walkthrough</h2>
      <ol>
        <li>Download Ollama from <a href='https://ollama.com/download' target='_blank' rel='noreferrer'>ollama.com/download</a>.</li>
        <li>Run the installer and let it finish adding the Ollama app and local service.</li>
        <li>Open Ollama once so the background service starts.</li>
        <li>Return to the AutoYou wizard and refresh the Ollama check.</li>
      </ol>
      <h2>If Ollama is installed but not responding</h2>
      <ul>
        <li>Launch the Ollama desktop app again.</li>
        <li>Or open a terminal and run <code>ollama serve</code>.</li>
        <li>Then install the starter model with <code>ollama pull ministral-3:8b</code> or use the AutoYou download button.</li>
      </ul>
      <h2>About Ollama cloud models</h2>
      <p><strong>Local models are recommended for AutoYou.</strong> They download once, run entirely on your device, and require no sign-in or subscription.</p>
      <p>Ollama recently added cloud model support, but cloud models require signing into your Ollama.com account via the command line:</p>
      <code style='display:block;margin:10px 0'>ollama login</code>
      <p>If you prefer local models (which we recommend), just ignore the cloud toggle in the model picker. Focus on installing a local model like <code>ministral-3:8b</code> or another option from the Ollama library.</p>
    </section>
    """


def build_connectivity_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Public Link &amp; Cloud Access</h1>
      <p>Remote AutoYou access depends on a paired phone or desktop being able to reach this server. There are three paths: a direct local-network connection, AutoYou Cloud, and a public link. Paired devices use Websites &amp; Browser &mdash; never the admin page.</p>
      <div class='guide-callout'>
        <strong>Before exposing anything:</strong> replace the initial pairing password with a strong, unique value and pick at least Secure mode. Keeping a known bootstrap password is risky once the server is reachable beyond localhost.
      </div>

      <h2>Path 1 &mdash; Local network (simplest)</h2>
      <p>When the server and phone are on the same Wi-Fi, no tunnel is needed. Find the server's LAN IP (<code>ipconfig getifaddr en0</code> on macOS, <code>ipconfig</code> on Windows) and point the phone at <code>http://[LAN-IP]:8067</code>. This is the lowest-latency option and ideal for home use.</p>
      <p>Direct LAN access requires the next AutoYou process to listen beyond loopback, for example <code>AUTOYOU_BIND_HOST=0.0.0.0</code> or <code>--host 0.0.0.0</code>. That is powerful and risky: it exposes the Admin UI, Auth Server, AI Agent server, and Websites &amp; Browser on the local network. Because Websites &amp; Browser also serves advertised agent websites and hosted pages, review those routes before using this path.</p>

      <h2>Path 2 &mdash; AutoYou Cloud (recommended for remote)</h2>
      <p>Cloud Pair helps phones and desktops reach this server when direct local access is blocked. Link this server from <strong>Connectivity &rarr; AutoYou Cloud &rarr; Link to AutoYou Cloud</strong>. Only the most recently activated linked server receives new pair requests, so use <strong>Make active here</strong> to move routing to this machine.</p>

      <h2>Path 3 &mdash; Public link</h2>
      <p>The public link gives this server a temporary HTTPS address, so a phone can reach it without Cloud Pair, messaging-based pairing, or router changes.</p>
      <div class='guide-callout'>
        <strong>Dangerous when left open:</strong> a public link publishes Websites &amp; Browser and pairing through an internet-reachable URL. Use Secure Professional, authenticator pair-code mode, URL-only sharing, and a timed lifetime. Never expose the admin page publicly.
      </div>
      <ol>
        <li>In <strong>Connectivity</strong>, enable the public link.</li>
        <li>Wait for a public <strong>Pair URL</strong> to appear.</li>
        <li>Share the Pair URL and the server password with the person pairing; they enter it under Proxy Settings.</li>
      </ol>
      <p>Host firewalls, antivirus, EDR tools, or corporate network policy can block the proxy. If the URL never appears, that does not always mean AutoYou is misconfigured. The free public URL also changes on each restart &mdash; for a persistent URL, use Cloud Pair instead.</p>
      <h3>Share a link only (most secure)</h3>
      <p>In Secure Professional mode with <strong>Authenticator</strong> pair-code mode, turn on <strong>Share URL only</strong> on the Connectivity screen. AutoYou then sends the other person <em>only</em> the link &mdash; no one-time code and no 2FA setup key travel in the message. They sign in with the shared 2FA setup key and password you hand over separately. Ideal for a classroom: share the link with everyone, tell the class the 2FA code and password out loud, and only your class can connect.</p>
      <h3>Keep the link online</h3>
      <p>Public Proxy subscribers get a persistent URL. Turn on <strong>Auto-connect public URL on startup</strong> and AutoYou brings that link back online by itself every time the server starts &mdash; hand it out once and leave it. Free servers can still enable the toggle, but it only takes effect once the plan is active.</p>

      <h2>Connection Helpers for stubborn networks</h2>
      <p>If a provider gives you connection helper details, paste the full helper snippet, JSON, env-style assignment, or server URLs into the Connection helpers box on the Connectivity screen. AutoYou normalizes and shares the bundle with paired devices.</p>
    </section>
    """


def build_telegram_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Telegram Bot Setup</h1>
      <p>Telegram is the lightest way to get one remote messaging partner connected.</p>
      <ol>
        <li>Create a bot with <a href='https://core.telegram.org/bots/features#creating-a-new-bot' target='_blank' rel='noreferrer'>BotFather</a>.</li>
        <li>Paste the bot token into AutoYou.</li>
        <li>Set your Telegram username in Telegram settings if you have not already.</li>
        <li>Restrict replies to your own username in AutoYou using the allowed usernames field.</li>
      </ol>
      <div class='guide-callout'>
        <strong>Security note:</strong> restricting replies to your own Telegram username is recommended even in normal security mode.
      </div>
    </section>
    """


def build_speech_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Speech Voices and STT Models</h1>
      <p>AutoYou keeps speech local by default. Text to speech uses <code>pyttsx3</code> when you pick the system provider, and live transcription uses RealtimeSTT with faster-whisper models underneath.</p>
      <div class='guide-callout'>
        <strong>Good default:</strong> keep system TTS enabled for offline replies, and pre-download the STT model you want so the first live call does not spend time warming the Whisper cache.
      </div>

      <h2 id='system-voices'>How system voices work</h2>
      <p><code>pyttsx3</code> uses the native speech engine on each host: SAPI5 on Windows, NSSpeechSynthesizer on macOS, and eSpeak on Linux-family systems. That means new voices come from the operating system, not from AutoYou itself.</p>

      <h3>Windows</h3>
      <ol>
        <li>Open <strong>Settings &gt; Time &amp; language &gt; Speech</strong> to review the installed voice.</li>
        <li>If you want more voices, add a language pack from <strong>Settings &gt; Time &amp; language &gt; Language &amp; region</strong> and include the speech components when available.</li>
        <li>Restart AutoYou after installing new voices so the dropdown can refresh.</li>
      </ol>
      <p>Microsoft reference: <a href='https://support.microsoft.com/windows/appendix-a-supported-languages-and-voices-4486e345-7730-53da-fcfe-55cc64300f01' target='_blank' rel='noreferrer'>supported Windows languages and voices</a>.</p>

      <h3>macOS</h3>
      <ol>
        <li>Open <strong>System Settings &gt; Accessibility &gt; Spoken Content</strong>.</li>
        <li>Choose <strong>System Voice</strong>, then download additional voices from the voice picker.</li>
        <li>Return to AutoYou and refresh the page after the download finishes.</li>
      </ol>
      <p>Apple reference: <a href='https://support.apple.com/en-vn/guide/mac-help/mchld0883d71/mac' target='_blank' rel='noreferrer'>change the spoken voice on Mac</a>.</p>

      <h3>Ubuntu, Debian, and Raspberry Pi OS</h3>
      <ol>
        <li>Install an eSpeak-compatible runtime such as <code>espeak-ng</code>.</li>
        <li>On Debian-family hosts, start with <code>sudo apt update &amp;&amp; sudo apt install espeak-ng</code>.</li>
        <li>Restart AutoYou so <code>pyttsx3</code> can re-enumerate the local voices.</li>
      </ol>
      <p>For Raspberry Pi OS, the Debian-family path above is usually the right starting point because the OS uses the same package ecosystem. Linux backend reference: <a href='https://pyttsx3.readthedocs.io/en/latest/support.html' target='_blank' rel='noreferrer'>pyttsx3 supported synthesizers</a>.</p>

      <h2 id='stt-models'>Speech-to-text models</h2>
      <p>AutoYou's STT field points at faster-whisper model names such as <code>tiny.en</code>, <code>small.en</code>, <code>large-v3</code>, or <code>distil-large-v3</code>. These are local Whisper-family models, and faster-whisper downloads them from Hugging Face the first time they are requested unless they are already cached.</p>
      <ul>
        <li><strong>Tiny / Base:</strong> fastest startup, best for lightweight CPUs.</li>
        <li><strong>Small / Medium:</strong> better accuracy with a moderate hardware cost.</li>
        <li><strong>Large v3 / Distil Large v3:</strong> best quality, especially on stronger machines or GPUs.</li>
      </ul>
      <p>The new speech model cache panel in AutoYou can pre-download those models ahead of time so calls start faster. Official references: <a href='https://github.com/SYSTRAN/faster-whisper' target='_blank' rel='noreferrer'>faster-whisper</a> and <a href='https://huggingface.co/docs/huggingface_hub/guides/download' target='_blank' rel='noreferrer'>Hugging Face download guide</a>.</p>

      <h2>Cloud TTS providers</h2>
      <p>If you switch away from the system voice provider, AutoYou can still use OpenAI TTS or Azure Speech. Those providers do not install local voices on the machine; they stream synthesized audio from the configured API.</p>
    </section>
    """


def build_video_calls_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Video &amp; Calls</h1>
      <p>Use <strong>Video &amp; Calls</strong> when a paired phone should send microphone or camera input, or when AutoYou should send this computer's audio, screen, camera, or a video file back to the call.</p>

      <h2>Computer video sources</h2>
      <ul>
        <li><strong>Remote Desktop</strong> shows this computer's screen natively in the video call. Enable <strong>Allow this computer's screen in video calls</strong> and <strong>Send this computer's screen</strong>, then choose the display, quality, and bitrate. The optional Remote Desktop app in Agents is a separate web console and is not required for the call feed.</li>
        <li><strong>Webcam / Camera</strong> uses a camera connected to this machine.</li>
        <li><strong>Video file</strong> plays a chosen local file into the call.</li>
        <li><strong>Realtime video input</strong> is for advanced local tools that send frames into AutoYou.</li>
      </ul>
      <div class='guide-callout'>
        <strong>Camera placeholder means the selected source is not ready.</strong> For Remote Desktop, enable both Video &amp; Calls switches, then refresh status. Native touch, fixed-pointer, and keyboard input additionally require <strong>Control Remote Desktop</strong> and an authenticated full-screen iOS or Android call.
      </div>

      <h2>Voice calls and training</h2>
      <p>Audio calls can receive the phone microphone, add this computer's microphone or loopback audio, transcribe speech, and play AutoYou replies back into the call. If <strong>Disable AutoYou Agents</strong> is on, calls and recordings can continue but transcription and AI replies stay off.</p>
      <p><strong>Wait Until I Finish Talking (WUIFT):</strong> If you want to bypass automated silence detection during calls, enable the WUIFT setting in your client. When active, silence gaps are ignored, allowing you to pause mid-thought. Simply press the floating green WUIFT button to manually finalize and flush everything you've said so far as a single completed turn.</p>
      <p><strong>Voice Training app does not have to be open</strong> for call samples to be saved. Turn on <strong>Record voice-call training samples</strong>; open the Voice Training app later when you want to review samples or train a custom voice.</p>

      <h2>Background phone connection</h2>
      <p><strong>Enable audio calls</strong> is the master switch for both Background Mode and Safety Recording. If audio calls are off, the server keeps both features off even if their individual switches are on.</p>
      <ul>
        <li><strong>Background Mode</strong> keeps a paired phone connected while its microphone stays off. On iOS, this uses receive-only background audio, so the microphone indicator stays off when no voice call is active.</li>
        <li><strong>Safety Recording</strong> is the separate opt-in that lets the phone send microphone audio to rotating files on this server. It does not send audio back, transcribe, or call AI; iOS's microphone indicator is expected while it records.</li>
        <li>Start a voice call first. Video and camera sharing are upgrades to that active voice call, not a separate microphone path.</li>
      </ul>
      <p>If the phone and server disagree, the safer server setting wins and the feature stays off.</p>

      <h2>Recording</h2>
      <ul>
        <li><strong>Safety recording</strong> creates microphone files only when the phone starts Safety Recording. AutoYou does not transcribe or answer that audio.</li>
        <li><strong>Record my video</strong> saves received phone camera frames during active video calls.</li>
        <li><strong>Record audio calls</strong> saves voice-call samples and transcripts for the Voice Training folder.</li>
      </ul>
      <p>The active folders are shown on Video &amp; Calls and Speech. Leave a folder field blank to use AutoYou's default data folder.</p>
    </section>
    """


def build_pairing_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Local Pair &amp; Bluetooth Pair</h1>
      <p>These are local-first ways to connect a phone near the computer running AutoYou.</p>

      <h2>Local Pair</h2>
      <ol>
        <li>Keep the phone and computer on the same Wi-Fi or network.</li>
        <li>On Overview, use <strong>Advertise to home network on next boot</strong> if the phone must reach this computer from another device.</li>
        <li>Shut down and restart AutoYou so the next-boot network mode takes effect.</li>
        <li>On the phone, choose <strong>Local Pair - same Wi-Fi / network</strong> and enter the address and port shown on Overview.</li>
      </ol>
      <div class='guide-callout'>
        <strong>Local-only is safest for admin.</strong> Home-network mode makes local AutoYou surfaces reachable from other devices on your network, so use a strong password and Secure mode before enabling it.
      </div>

      <h2>Bluetooth Pair</h2>
      <p>Bluetooth Pair lets nearby phones discover this computer without exposing the admin website. The server password and 2FA still apply, and the saved computer name is only a label on the phone.</p>
      <ol>
        <li>Turn on <strong>Allow Bluetooth Pair</strong> in Overview or Connectivity.</li>
        <li>Leave it on only while you expect nearby phones to pair.</li>
        <li>If the listener is not running, install or start the Bluetooth requirements shown in the status message.</li>
      </ol>
    </section>
    """


def build_architecture_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Architecture &amp; Ports</h1>
      <p>AutoYou is a self-hosted AI server that runs on your own machine &mdash; macOS, Windows, or Linux. Paired phones and desktops connect through app links or a direct local-network connection. No conversation data reaches a third party unless you explicitly configure a cloud AI provider.</p>

      <h2>Core components</h2>
      <ul>
        <li><strong>Admin shell</strong> &mdash; the browser configuration UI you are reading this in: AI providers, models, agents, connectivity, security, messaging, and speech.</li>
        <li><strong>AutoYou AI</strong> &mdash; the assistant engine that can use Ollama, Gemini, Claude, OpenAI, DeepSeek, or OpenClaw.</li>
        <li><strong>Websites &amp; Browser</strong> &mdash; the app-facing website service for paired phones and desktops; it also serves agent websites and hosted pages.</li>
      </ul>

      <h2>Default ports</h2>
      <ul>
        <li><code>8001</code> &mdash; Admin web shell. <strong>Inbound, local only. Never expose to the internet.</strong> Login required.</li>
        <li><code>8067</code> &mdash; Websites &amp; Browser. Paired devices connect here over LAN, Cloud Pair, or the public link.</li>
        <li><code>8081</code> &mdash; AutoYou AI.</li>
        <li><code>8002</code> &mdash; Pairing handoff service used while a device connects.</li>
        <li><code>11434</code> &mdash; Ollama API (local only; only if Ollama is installed).</li>
        <li><code>8082</code> / <code>8083</code> &mdash; optional Signal / WhatsApp Docker services.</li>
      </ul>
      <div class='guide-callout'>
        <strong>Golden rule:</strong> the admin page must never be exposed to the internet. Remote paired devices use Websites &amp; Browser only &mdash; via Cloud Pair or the public link.
      </div>
    </section>
    """


def build_ai_providers_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>AI Providers</h1>
      <p>AutoYou can use a local model or a cloud provider. Pick the path in <strong>AI &amp; Models</strong>.</p>

      <h2>AI paths</h2>
      <ul>
        <li><strong>Ollama + Agents</strong> &mdash; fully local, offline, no API key. The privacy-first default. See the <strong>Ollama Install</strong> guide.</li>
        <li><strong>Gemini + Agents</strong> &mdash; Google Gemini via API key. Online.</li>
        <li><strong>Other AI Providers + Agents</strong> &mdash; Anthropic Claude, OpenAI, DeepSeek, Mistral, Azure, or any OpenAI-compatible endpoint via API key.</li>
        <li><strong>OpenClaw</strong> &mdash; uses a live, already-signed-in Claude or Gemini browser session on the server machine instead of an API key.</li>
      </ul>
      <p>Except for OpenClaw, AutoYou agents stay active with the selected provider. Switching providers does not affect conversation history, agent settings, or Websites &amp; Browser state.</p>

      <div class='guide-callout'>
        <strong>Privacy note:</strong> with a cloud provider, your prompts and conversation content are sent to that provider's API and leave your machine. Local Ollama keeps everything on-device.
      </div>

      <h2>OpenClaw setup</h2>
      <p>Install the OpenClaw browser extension in the server machine's Chrome, sign into the AI web service there, then select OpenClaw in AI &amp; Models. Responses are captured from that signed-in browser tab and relayed to AutoYou, subject to that web service's rate limits and terms.</p>
    </section>
    """


def build_agents_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Agent System</h1>
      <p>AutoYou runs one <strong>main agent</strong> plus any number of installable <strong>sub-agents</strong>. The main agent's system prompt (edited at the top of the <strong>Agents</strong> screen) governs the assistant's overall personality and sits above every sub-agent.</p>

      <h2>Main agent vs sub-agents</h2>
      <ul>
        <li><strong>Main agent</strong> &mdash; always present. Edit its root system prompt in section-builder mode (guided) or raw mode (direct).</li>
        <li><strong>Sub-agents</strong> &mdash; specialist agents (admin, internet search, and more) you install or uninstall. Installed sub-agents become available to AutoYou AI; available ones are ready to install.</li>
      </ul>

      <h2>Lifecycle</h2>
      <ol>
        <li><strong>Install</strong> a built-in sub-agent, or <strong>Create New Agent</strong> to scaffold a draft on disk.</li>
        <li><strong>Configure</strong> its instructions and optional website in Agent Studio.</li>
        <li><strong>Publish</strong> workspace changes when you are in an editable workspace.</li>
        <li><strong>Restart AutoYou AI</strong> after installs or provider changes so the new state loads.</li>
      </ol>

      <h2>Common built-in apps</h2>
      <ul>
        <li><strong>Internet Search</strong> &mdash; turn this on in AI &amp; Models before asking for live web results. Turn it off for offline-only answers.</li>
        <li><strong>Remote Desktop</strong> &mdash; install the app and select Remote Desktop in Video &amp; Calls before phones can see this computer's screen. The Agents switch only controls the web console.</li>
        <li><strong>Voice Training</strong> &mdash; use it to review saved call samples and train a custom voice. Recording samples does not require this app page to be open.</li>
        <li><strong>Ads Watching</strong> &mdash; shows the watch flow when an eligible device opens it. Keep it off when you do not want that flow available.</li>
        <li><strong>Browser Control</strong> &mdash; lets approved prompts open a URL, go home, reload, go back, or go forward on the connected phone/browser session.</li>
      </ul>

      <div class='guide-callout'>
        <strong>Installed app:</strong> only the agents included in the app can be installed or uninstalled. You can still create and edit Workspace Drafts, but publishing and live testing are disabled here. Move a custom draft into an editable workspace, test it locally, then rebuild the app. The root prompt can still be changed with Prompt Override.
      </div>
    </section>
    """


def build_agent_studio_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Agent Studio</h1>
      <p>Agent Studio is where you manage one agent at a time. Use the <strong>Studio agent</strong> selector at the top of the studio panel to switch agents in place &mdash; it defaults to the AutoYou main agent (marked <strong>Main</strong>).</p>

      <h2>Studio tabs</h2>
      <ul>
        <li><strong>Build &amp; Publish</strong> &mdash; install or uninstall agents, inspect the installed copy, and publish workspace drafts when allowed.</li>
        <li><strong>Agent Instructions</strong> &mdash; edit the selected agent's instructions. Changes are local until published.</li>
        <li><strong>Agent Website</strong> &mdash; enable or disable the agent's app page and choose how it appears through Websites &amp; Browser.</li>
      </ul>

      <h2>Drafts &amp; publishing</h2>
      <p>Workspace drafts stay isolated until you publish them, so you can edit safely without affecting the installed agent. In an editable workspace, create or clone a draft, test it locally, and publish it when it is ready.</p>
    </section>
    """


def build_page_service_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Websites &amp; Browser</h1>
      <p>Websites &amp; Browser is the local website service on port <code>8067</code>. It hosts agent app pages, saved shortcuts, and any local sites you choose to advertise.</p>

      <h2>What it hosts</h2>
      <ul>
        <li><strong>Agent app pages</strong> &mdash; each agent with a UI gets its own browser page.</li>
        <li><strong>Advertised websites</strong> &mdash; add a local web service with a label and description.</li>
        <li><strong>Browser routes</strong> &mdash; choose which page opens first for paired phones and desktops.</li>
      </ul>
      <p>Configure advertised websites and routes from the <strong>Websites &amp; Browser</strong> screen. Each route stays behind AutoYou's selected connection path, so a local site does not need its own public URL.</p>
    </section>
    """


def build_security_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Security Modes &amp; 2FA</h1>
      <p>AutoYou has four security modes, set from <strong>Security</strong> (and in Setup &amp; Boot). The mode affects pairing authentication and, in Maximus, protection for saved data on this computer.</p>

      <h2>Modes</h2>
      <ul>
        <li><strong>Normal</strong> &mdash; password only. Pairing messages stay plaintext. Use only for trusted local-first setups.</li>
        <li><strong>Secure</strong> &mdash; encrypts pairing messages with the server password. The recommended default once you go remote.</li>
        <li><strong>Secure Professional</strong> &mdash; password plus one shared 2FA setup key, used for Secure Professional pairing, authenticator pair-code mode, and admin-agent elevation.</li>
        <li><strong>Secure Professional Maximus</strong> &mdash; Secure Professional pairing plus protection for saved sessions, agent data, websites, notes, and settings on this computer. It keeps the same pairing and remote permission controls.</li>
      </ul>

      <div class='guide-callout'>
        <strong>Change the initial pairing password.</strong> Choose a strong, unique value in Security (or Setup &amp; Boot) before connecting any client or enabling remote access.
      </div>

      <h2>Shared authenticator</h2>
      <p>AutoYou keeps a single shared 2FA setup key. Generate it, show its QR, or import an existing authenticator setup key from the Security screen. The same setup key powers Secure Professional pairing and admin elevation, so a phone authenticator app can produce valid codes for all of them.</p>
    </section>
    """


def build_zero_touch_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Automatic Configuration QR</h1>
      <p>An Automatic Configuration QR encodes a full client connection profile &mdash; server name, connection details, helper list, security mode, and (optionally) the shared authenticator &mdash; so a supported client can apply everything in one scan.</p>

      <h2>Export it</h2>
      <ol>
        <li>Open <strong>Security</strong> (or the Security &amp; 2FA step in Setup &amp; Boot).</li>
        <li>Click <strong>Export Automatic Configuration Setup QR</strong>.</li>
        <li>Show or print the QR, then scan it from the client's Automatic Configuration / Connection flow.</li>
      </ol>

      <div class='guide-callout'>
        <strong>Treat it like a password.</strong> The QR can carry pairing secrets, so never share it publicly or over insecure channels. Re-export after a password or security-mode change so the profile stays current.
      </div>
    </section>
    """


def build_webrtc_ice_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Connection Helpers</h1>
      <p>AutoYou uses one encrypted app connection for messages and voice calls. Connection helpers give clients another path when direct network setup fails.</p>

      <h2>What they do</h2>
      <p>Connection helpers are optional provider details that help calls and connected clients work on stricter mobile, hotel, corporate, or home networks. Cloud Pair manages this automatically for hosted connections; self-managed setups can paste provider details on the Connectivity screen.</p>

      <h2>Editing Connection Helpers</h2>
      <ol>
        <li>On <strong>Connectivity &rarr; Connection helpers</strong>, paste provider details, JSON, or connection server URLs into Quick import.</li>
        <li>Use <strong>Add to existing</strong> to merge, or <strong>Replace from input</strong> to overwrite.</li>
        <li>Review the resolved JSON, then <strong>Save helpers</strong>. AutoYou preserves your exact structure.</li>
      </ol>
    </section>
    """


def build_config_files_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Config &amp; Data Files</h1>
      <p>AutoYou stores its state under the server's application data directory. The exact paths are shown across the admin (for example, the Agents and Drafts roots on the Agents screen).</p>

      <h2>What lives where</h2>
      <ul>
        <li><strong>Server config</strong> &mdash; AI provider, ports, security mode, messaging tokens, and speech settings. Managed through the admin; an encrypted keystore protects secrets.</li>
        <li><strong>Agents</strong> &mdash; installed agent directories plus an isolated drafts root for workspace edits.</li>
        <li><strong>Data</strong> &mdash; conversation history (when recording is enabled), notes, and hosted page content.</li>
        <li><strong>Models</strong> &mdash; cached local speech-to-text models.</li>
      </ul>

      <div class='guide-callout'>
        <strong>Edit through the admin, not by hand.</strong> Changing config files while the server is running can be overwritten on the next save or cause a restart failure. To back up, stop the server and copy the whole application data directory.
      </div>
    </section>
    """


def build_troubleshooting_guide_html() -> str:
    return """
    <section class='guide-shell'>
      <h1>Troubleshooting</h1>

      <h2>Server won't start, or a blank screen after login</h2>
      <ul>
        <li>Confirm the admin port is free: <code>lsof -i :8001</code> (macOS/Linux) or <code>netstat -ano | findstr 8001</code> (Windows).</li>
        <li>Check the terminal/console for Python import errors &mdash; a missing dependency will block startup in a source checkout.</li>
        <li>On macOS, if Gatekeeper quarantine blocks a downloaded binary, clear it with <code>xattr -dr com.apple.quarantine</code> on the app.</li>
      </ul>

      <h2>A phone or desktop can't connect</h2>
      <ul>
        <li>Make sure the device targets Websites &amp; Browser, not the admin page.</li>
        <li>For LAN, confirm both devices are on the same network and the firewall allows <code>8067</code>.</li>
        <li>For remote, confirm Cloud Pair is linked and active, or that the public link shows a Pair URL.</li>
        <li>On iOS, grant the Local Network permission the first time it is requested.</li>
      </ul>

      <h2>Pairing fails or times out</h2>
      <ul>
        <li>Confirm the client is using the current server password (and re-export the Zero Touch QR after a password change).</li>
        <li>If a messaging partner is involved, confirm it shows Connected in Messaging.</li>
        <li>If direct connection keeps failing, add connection helpers or use Cloud Pair.</li>
      </ul>

      <h2>AutoYou AI is not responding</h2>
      <ul>
        <li>Restart AutoYou AI from the Agents or AI &amp; Models screen.</li>
        <li>For Ollama, confirm the local service is running (<code>ollama serve</code>) and the model is pulled.</li>
        <li>For cloud providers, confirm the API key is set and has quota.</li>
      </ul>
    </section>
    """


def admin_doc_guides() -> List[Dict[str, Any]]:
    """Ordered registry of native, server-owned documentation pages.

    Each entry's ``builder`` returns the body HTML for the page; the same body
    feeds both the standalone ``/guides/doc/<id>`` page and the in-shell reader
    content endpoint. Pure-Python content ships in every packaged binary.
    """
    return [
        {
            "id": "architecture",
            "title": "Architecture & Ports",
            "subtitle": "How AutoYou is laid out and which ports each service uses.",
            "category": "Core docs",
            "builder": build_architecture_guide_html,
        },
        {
            "id": "ai-providers",
            "title": "AI Providers",
            "subtitle": "Local Ollama, cloud providers, and the OpenClaw browser gateway.",
            "category": "Core docs",
            "builder": build_ai_providers_guide_html,
        },
        {
            "id": "agents",
            "title": "Agent System",
            "subtitle": "The main agent, installable sub-agents, and the install/publish lifecycle.",
            "category": "Core docs",
            "builder": build_agents_guide_html,
        },
        {
            "id": "video-calls",
            "title": "Video & Calls",
            "subtitle": "Remote Desktop video, phone camera/audio, recording, and voice training capture.",
            "category": "Core docs",
            "builder": build_video_calls_guide_html,
        },
        {
            "id": "local-pair-bluetooth",
            "title": "Local Pair & Bluetooth Pair",
            "subtitle": "Same-network pairing, next-boot network mode, and nearby Bluetooth discovery.",
            "category": "Core docs",
            "builder": build_pairing_guide_html,
        },
        {
            "id": "agent-studio",
            "title": "Agent Studio",
            "subtitle": "Switch agents, edit instructions, build/publish, and configure agent app pages.",
            "category": "Core docs",
            "builder": build_agent_studio_guide_html,
        },
        {
            "id": "page-service",
            "title": "Websites & Browser",
            "subtitle": "Local website service on port 8067: agent app pages, advertised sites, and routes.",
            "category": "Core docs",
            "builder": build_page_service_guide_html,
        },
        {
            "id": "security-modes",
            "title": "Security Modes & 2FA",
            "subtitle": "Normal, Secure, Secure Professional, and Maximus, plus the shared authenticator setup.",
            "category": "Core docs",
            "builder": build_security_guide_html,
        },
        {
            "id": "zero-touch",
            "title": "Automatic Configuration QR",
            "subtitle": "One-scan phone/desktop configuration, and when to re-export it.",
            "category": "Core docs",
            "builder": build_zero_touch_guide_html,
        },
        {
            "id": "webrtc-ice",
            "title": "Connection Helpers",
            "subtitle": "Optional helper details for strict networks and remote paired devices.",
            "category": "Core docs",
            "builder": build_webrtc_ice_guide_html,
        },
        {
            "id": "config-files",
            "title": "Config & Data Files",
            "subtitle": "Where config, agents, data, and models live, and how to back them up.",
            "category": "Core docs",
            "builder": build_config_files_guide_html,
        },
        {
            "id": "troubleshooting",
            "title": "Troubleshooting",
            "subtitle": "Startup, connection, pairing, and AutoYou AI problems and fixes.",
            "category": "Core docs",
            "builder": build_troubleshooting_guide_html,
        },
    ]
