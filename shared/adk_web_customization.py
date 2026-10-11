# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Customize staged Google ADK Web assets without changing the installed package."""

from __future__ import annotations

import re
from pathlib import Path


ADK_WEB_PATCH_SENTINEL = "AUTOYOU_ADK_WEB_UI_PATCH_V2"
ADK_WEB_ROW_SENTINEL = "AUTOYOU_ADK_HIDE_USAGE_METRICS_ROW"
ADK_WEB_MODIFICATION_NOTICE = (
    "Modified by AutoYou: removes the ADK Web usage-metrics control and "
    "telemetry consent UI. The upstream development-use disclosure is retained. "
    "Google ADK Web remains separately licensed; this notice does not imply "
    "Google endorsement."
)
ADK_WEB_FORBIDDEN_BUNDLE_TEXT = (
    "Usage Metrics",
    "telemetry-consent-dialog-panel",
    "/config/telemetry",
    "X-ADK-Telemetry-Request",
)

_METHOD_REPLACEMENTS = {
    "checkTelemetryConsent": "return Promise.resolve();",
    "onTelemetryToggle": "return Promise.resolve();",
    "fetchTelemetryStatus": "return Promise.resolve(false);",
    "setTelemetry": "return Promise.resolve();",
}
_FEATURE_MARKERS = (
    "Usage Metrics",
    "telemetry-consent-dialog-panel",
    "/config/telemetry",
    "X-ADK-Telemetry-Request",
    "checkTelemetryConsent",
    "onTelemetryToggle",
    "fetchTelemetryStatus",
    "setTelemetry",
)


class AdkWebUiPatchError(RuntimeError):
    """The ADK Web asset layout no longer matches the reviewed patch contract."""


def _find_matching_brace(source: str, opening: int) -> int:
    """Find a JavaScript block's closing brace, skipping strings and comments."""
    depth = 0
    index = opening
    while index < len(source):
        if source.startswith("//", index):
            end = source.find("\n", index + 2)
            index = len(source) if end < 0 else end
            continue
        if source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = len(source) if end < 0 else end + 2
            continue
        char = source[index]
        if char in ("'", '"', "\x60"):
            quote = char
            index += 1
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise AdkWebUiPatchError("Could not parse a JavaScript method body in ADK Web.")


def _replace_method_body(source: str, name: str, body: str) -> tuple[str, int]:
    """Replace a minified class method by its stable method name, not local symbols."""
    pattern = re.compile(rf"\b{re.escape(name)}\s*\([^)]*\)\s*\{{")
    matches = list(pattern.finditer(source))
    if len(matches) > 1:
        raise AdkWebUiPatchError(
            f"Found multiple ADK Web declarations for {name}(); review the upstream bundle."
        )
    if not matches:
        return source, 0

    match = matches[0]
    opening = match.end() - 1
    closing = _find_matching_brace(source, opening)
    return source[: opening + 1] + body + source[closing:], 1


def _replace_usage_label(source: str) -> tuple[str, int]:
    pattern = re.compile(r"(['\"])Usage Metrics\1", re.IGNORECASE)
    updated, count = pattern.subn(
        lambda match: f"{match.group(1)}{ADK_WEB_ROW_SENTINEL}{match.group(1)}",
        source,
    )
    if "Usage Metrics" in updated:
        raise AdkWebUiPatchError(
            "Found an ADK Web usage-metrics label that is not a supported string literal; "
            "review the updated frontend before packaging."
        )
    return updated, count


def _modified_js_banner() -> str:
    return f"/* {ADK_WEB_PATCH_SENTINEL}. {ADK_WEB_MODIFICATION_NOTICE} */\n"


def _modified_html_banner() -> str:
    return f"<!-- {ADK_WEB_PATCH_SENTINEL}. {ADK_WEB_MODIFICATION_NOTICE} -->\n"


def _usage_row_script() -> str:
    return f"""<script id="{ADK_WEB_PATCH_SENTINEL}">
(() => {{
  const marker = "{ADK_WEB_ROW_SENTINEL}";
  const removeUsageRow = () => {{
    for (const node of document.querySelectorAll("*")) {{
      if (node.children.length || node.textContent.trim() !== marker) continue;
      let row = node;
      while (row.parentElement && row.parentElement !== document.body) {{
        const parent = row.parentElement;
        if (parent.querySelector('mat-slide-toggle,[role="switch"]')) {{
          parent.remove();
          break;
        }}
        row = parent;
      }}
    }}
  }};
  removeUsageRow();
  new MutationObserver(removeUsageRow).observe(document.documentElement, {{
    childList: true,
    subtree: true
  }});
}})();
</script>
"""


def _verify_clean_assets(root: Path) -> None:
    js_files = sorted(root.rglob("*.js"))
    remaining: list[str] = []
    for path in js_files:
        text = path.read_text(encoding="utf-8")
        remaining.extend(
            marker
            for marker in ADK_WEB_FORBIDDEN_BUNDLE_TEXT
            if marker.casefold() in text.casefold()
        )
    if remaining:
        raise AdkWebUiPatchError(
            "ADK Web still contains telemetry controls or requests: "
            + ", ".join(sorted(set(remaining)))
        )
    unsupported_assets: list[Path] = []
    for path in root.rglob("*"):
        if path.suffix.lower() not in {".html", ".json"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if any(
            marker.casefold() in text.casefold()
            for marker in ADK_WEB_FORBIDDEN_BUNDLE_TEXT
        ):
            unsupported_assets.append(path)
    if unsupported_assets:
        raise AdkWebUiPatchError(
            "ADK Web telemetry controls moved into non-JavaScript assets that "
            "need a reviewed patch: "
            + ", ".join(str(path) for path in unsupported_assets)
        )


def verify_adk_web_ui_assets(browser_dir: str | Path) -> None:
    """Verify the staged ADK Web assets contain no known telemetry UI or calls."""
    root = Path(browser_dir)
    if not root.is_dir():
        raise AdkWebUiPatchError(f"ADK Web browser directory is missing: {root}")
    if not list(root.rglob("*.js")):
        raise AdkWebUiPatchError(f"ADK Web JavaScript assets are missing: {root}")
    if not (root / "index.html").is_file():
        raise AdkWebUiPatchError(f"ADK Web index.html is missing: {root / 'index.html'}")
    _verify_clean_assets(root)

    for path in root.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        if ADK_WEB_PATCH_SENTINEL in text and ADK_WEB_MODIFICATION_NOTICE not in text:
            raise AdkWebUiPatchError(
                f"Modified ADK Web bundle is missing its modification notice: {path}"
            )
    index = (root / "index.html").read_text(encoding="utf-8")
    if ADK_WEB_PATCH_SENTINEL in index:
        if ADK_WEB_MODIFICATION_NOTICE not in index:
            raise AdkWebUiPatchError(
                "Modified ADK Web index.html is missing its modification notice."
            )
        if ADK_WEB_ROW_SENTINEL not in index:
            raise AdkWebUiPatchError(
                "ADK Web usage-row removal script is incomplete."
            )


def patch_adk_web_ui_assets(browser_dir: str | Path) -> bool:
    """Patch an AutoYou-owned staging copy when recognizable telemetry controls exist.

    This is version-independent for bundles that retain these semantic anchors:
    it keys off the user-facing label and stable frontend method names, not
    release numbers or minifier-generated symbols. Recognized but unsupported
    structures fail closed. A bundle with none of the known markers is treated
    as already clean, so maintainers must review ADK UI changes before updating
    the dependency pin.

    Returns True when assets were changed, or False when they were already clean.
    """
    root = Path(browser_dir)
    if not root.is_dir():
        raise AdkWebUiPatchError(f"ADK Web browser directory is missing: {root}")
    js_files = sorted(root.rglob("*.js"))
    if not js_files:
        raise AdkWebUiPatchError(f"ADK Web JavaScript assets are missing: {root}")
    index_path = root / "index.html"
    if not index_path.is_file():
        raise AdkWebUiPatchError(f"ADK Web index.html is missing: {index_path}")

    index = index_path.read_text(encoding="utf-8")
    sources = {path: path.read_text(encoding="utf-8") for path in js_files}
    non_js_features = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".html", ".json"}
        and any(
            marker.casefold() in path.read_text(encoding="utf-8").casefold()
            for marker in ADK_WEB_FORBIDDEN_BUNDLE_TEXT
        )
    ]
    if non_js_features:
        raise AdkWebUiPatchError(
            "ADK Web telemetry controls moved into non-JavaScript assets that "
            "need a reviewed patch: "
            + ", ".join(str(path) for path in non_js_features)
        )
    patched_js = any(ADK_WEB_PATCH_SENTINEL in source for source in sources.values())
    patched_row = any(ADK_WEB_ROW_SENTINEL in source for source in sources.values())
    patched_index = ADK_WEB_PATCH_SENTINEL in index
    if patched_js or patched_index:
        if patched_row != patched_index or (patched_index and not patched_js):
            raise AdkWebUiPatchError("ADK Web assets contain a partial AutoYou patch.")
        verify_adk_web_ui_assets(root)
        return False

    combined = "\n".join(sources.values())
    feature_present = any(marker.casefold() in combined.casefold() for marker in _FEATURE_MARKERS)
    if not feature_present:
        verify_adk_web_ui_assets(root)
        return False

    changed_files: set[Path] = set()
    label_count = 0
    method_counts = dict.fromkeys(_METHOD_REPLACEMENTS, 0)
    for path, source in sources.items():
        updated, count = _replace_usage_label(source)
        label_count += count
        if updated != source:
            source = updated
            changed_files.add(path)
        for name, body in _METHOD_REPLACEMENTS.items():
            updated, count = _replace_method_body(source, name, body)
            method_counts[name] += count
            if updated != source:
                source = updated
                changed_files.add(path)
        sources[path] = source

    if "Usage Metrics" in combined and label_count == 0:
        raise AdkWebUiPatchError(
            "ADK Web's usage-metrics label changed shape; review the updated frontend."
        )
    if method_counts["onTelemetryToggle"] and label_count == 0:
        raise AdkWebUiPatchError(
            "ADK Web's usage control label moved away from its toggle handler; "
            "review the updated frontend."
        )
    if "telemetry-consent-dialog-panel" in combined and not method_counts["checkTelemetryConsent"]:
        raise AdkWebUiPatchError(
            "ADK Web consent dialog moved without a recognized handler; review the updated frontend."
        )
    if "/config/telemetry" in combined and not method_counts["fetchTelemetryStatus"]:
        raise AdkWebUiPatchError(
            "ADK Web telemetry status request moved without a recognized handler; "
            "review the updated frontend."
        )
    if "X-ADK-Telemetry-Request" in combined and not method_counts["setTelemetry"]:
        raise AdkWebUiPatchError(
            "ADK Web telemetry setting request moved without a recognized handler; "
            "review the updated frontend."
        )
    if ("Usage Metrics" in combined or ADK_WEB_ROW_SENTINEL in combined) and not (
        method_counts["onTelemetryToggle"] or method_counts["setTelemetry"]
    ):
        raise AdkWebUiPatchError(
            "ADK Web usage control changed shape; review the updated frontend."
        )
    if not changed_files:
        raise AdkWebUiPatchError(
            "ADK Web telemetry markers were found, but no supported controls were patched."
        )

    for path in changed_files | ({index_path} if label_count else set()):
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise AdkWebUiPatchError(
                f"Refusing to customize an ADK asset linked outside the staging copy: {path}"
            )

    for path in changed_files:
        source = sources[path]
        sources[path] = _modified_js_banner() + source

    if label_count:
        if ADK_WEB_PATCH_SENTINEL in index:
            raise AdkWebUiPatchError("ADK Web index already contains a partial AutoYou patch.")
        head = re.search(r"<head\b[^>]*>", index, flags=re.IGNORECASE)
        if not head:
            raise AdkWebUiPatchError(
                "ADK Web index has no head element for the usage-row removal script."
            )
        insertion = _modified_html_banner() + _usage_row_script()
        index = index[: head.end()] + insertion + index[head.end() :]
        index_path.write_text(index, encoding="utf-8")

    for path in changed_files:
        path.write_text(sources[path], encoding="utf-8")
    verify_adk_web_ui_assets(root)
    return True
