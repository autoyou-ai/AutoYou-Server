#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Collect reviewed ADK Web dependency notices without executing npm packages.

This is a conservative notice inventory from a matched upstream lock file,
not a resolved binary SBOM. Future ADK updates require a new provenance review.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
from urllib.parse import quote
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_RELEASE = "v1.0.7"
WEB_COMMIT = "34f3f8d7946c76faa664658390dcccf1e4e34677"
LOCK_URL = f"https://raw.githubusercontent.com/google/adk-web/{WEB_COMMIT}/package-lock.json"
VIZ_COMMIT = "96be4ed32789125f00cffa3ae390bd8809fc9c03"
SUPPLEMENTAL = {
    "Viz.js MIT license": f"https://raw.githubusercontent.com/mdaines/viz-js/{VIZ_COMMIT}/LICENSE",
    "Graphviz 15.1.1 EPL-2.0 license": "https://gitlab.com/graphviz/graphviz/-/raw/15.1.1/COPYING",
    "Expat 2.8.1 MIT license": "https://raw.githubusercontent.com/libexpat/libexpat/R_2_8_1/expat/COPYING",
    "Emscripten 5.0.5 license and notices": "https://raw.githubusercontent.com/emscripten-core/emscripten/5.0.5/LICENSE",
    "musl libc notices in Emscripten 5.0.5": "https://raw.githubusercontent.com/emscripten-core/emscripten/5.0.5/system/lib/libc/musl/COPYRIGHT",
}


def fetch(url: str) -> bytes:
    with urlopen(url, timeout=60) as response:
        return response.read()


def package_notices(item: tuple[str, str, str]) -> dict:
    name, version, license_expression = item
    metadata_url = f"https://registry.npmjs.org/{quote(name, safe='@/')}/{quote(version, safe='')}"
    metadata = json.loads(fetch(metadata_url))
    tarball_url = metadata["dist"]["tarball"]
    if not tarball_url.startswith("https://registry.npmjs.org/"):
        raise ValueError(f"Unexpected registry tarball URL for {name}")
    tarball = fetch(tarball_url)
    integrity = metadata["dist"].get("integrity", "")
    if not integrity.startswith("sha512-") or base64.b64encode(hashlib.sha512(tarball).digest()).decode() != integrity[7:]:
        raise ValueError(f"Registry integrity mismatch for {name}@{version}")
    notices = []
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as archive:
        for member in archive.getmembers():
            basename = Path(member.name).name
            if not member.isfile() or not re.match(r"^(licen[cs]e|notice|copying|copyright)(?:[._-]|$)", basename, re.I):
                continue
            if Path(basename).suffix.lower() in {".js", ".ts", ".map"}:
                continue
            content = archive.extractfile(member)
            if content is not None:
                notices.append({"path": member.name, "text": content.read().decode("utf-8")})
        if not notices and name in {"fastdom", "strictdom"}:
            readme = archive.extractfile("package/README.md").read().decode("utf-8")
            notice = readme[readme.index("## License"):]
            if "Permission is hereby granted" not in notice:
                raise ValueError(f"Reviewed README license changed for {name}")
            notices.append({"path": "package/README.md (license section)", "text": notice})
        if not notices and name == "@sphinxxxx/color-conversion":
            source = archive.extractfile("package/dist/color-conversion.js").read().decode("utf-8")
            header = source[:source.index("*/") + 2]
            if "Copyright 2017-2019 Joudee" not in header or "ISC license" not in header:
                raise ValueError("Reviewed color-conversion notice changed")
            canonical = fetch("https://raw.githubusercontent.com/spdx/license-list-data/main/text/ISC.txt").decode("utf-8")
            notices.append({"path": "package/dist/color-conversion.js (original header and standard ISC terms)", "text": header + "\n\n" + canonical[canonical.index("Permission"): ]})
        if not notices and name == "natural-compare-lite":
            readme = archive.extractfile("package/README.md").read().decode("utf-8")
            notice = readme[readme.index("Licence\n-------"):]
            canonical = fetch("https://raw.githubusercontent.com/spdx/license-list-data/main/text/MIT.txt").decode("utf-8")
            notices.append({"path": "package/README.md (original attribution and standard MIT terms)", "text": notice + "\n\n" + canonical[canonical.index("Permission"): ]})
        if not notices and name in {"is-reference", "locate-character"}:
            readme = archive.extractfile("package/README.md").read().decode("utf-8")
            if "## License\n\nMIT" not in readme or metadata.get("author", {}).get("name") != "Rich Harris":
                raise ValueError(f"Reviewed MIT declaration changed for {name}")
            canonical = fetch("https://raw.githubusercontent.com/spdx/license-list-data/main/text/MIT.txt").decode("utf-8")
            notice = ("Upstream README and package metadata declare MIT. Author metadata: Rich Harris.\n"
                      "The published package has no standalone license file or explicit copyright notice.\n"
                      "The following is the standard MIT permission and disclaimer text.\n\n")
            notices.append({"path": "package/README.md and npm author metadata (standard MIT terms)", "text": notice + canonical[canonical.index("Permission"): ]})
    return {
        "name": name, "version": version, "licenseExpression": license_expression,
        "tarball": tarball_url, "integrity": integrity,
        "sha256": hashlib.sha256(tarball).hexdigest(), "notices": notices,
    }


def main() -> None:
    lock_bytes = fetch(LOCK_URL)
    lock = json.loads(lock_bytes)
    packages = set()
    for path, item in lock["packages"].items():
        if not path or item.get("dev") or not item.get("version"):
            continue
        name = item.get("name") or path.rsplit("node_modules/", 1)[-1]
        packages.add((name, item["version"], str(item.get("license", "UNSPECIFIED"))))
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(package_notices, sorted(packages)))
    missing = [f"{item['name']}@{item['version']}" for item in results if not item["notices"] and item["name"] != "@viz-js/viz"]
    output_dir = REPO_ROOT / "docs/legal/licenses"
    output_dir.mkdir(parents=True, exist_ok=True)
    provenance = {
        "adkVersion": "2.11.0", "webRelease": WEB_RELEASE, "webSourceCommit": WEB_COMMIT,
        "lockUrl": LOCK_URL, "lockSha256": hashlib.sha256(lock_bytes).hexdigest(),
        "webArchive": "https://github.com/google/adk-web/releases/download/v1.0.7/adk-web-browser.zip",
        "webArchiveSha256": "f26ad5d04197fd1cfb47e62857969977933030de02b1d509468c64efcb2997fa",
        "assetMatch": "All 113 archive files matched the ADK 2.11.0 wheel browser files by SHA256 during review.",
        "scope": "Conservative non-dev lockfile notice inventory; may include unused packages; not a binary SBOM.",
        "supplementalNoticeUrls": SUPPLEMENTAL,
        "packages": [{key: value for key, value in item.items() if key != "notices"} for item in results],
        "missingNotices": missing,
    }
    (output_dir / "google-adk-web-notice-provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    if missing:
        raise RuntimeError("Missing license files need source review: " + ", ".join(missing))
    sections = [
        "Google ADK Web frontend third-party notices",
        "ADK Python 2.11.0 embeds ADK Web v1.0.7, built from " + WEB_COMMIT + ".",
        "This is a conservative non-dev lockfile notice inventory. It may include unused packages and is not a binary SBOM.",
        "Original third-party terms below apply to their respective components; AutoYou's license does not replace them.",
        "",
        "Graphviz source availability (EPL-2.0)",
        "AutoYou does not modify the Graphviz or Expat sources embedded by Viz.js.",
        "Recipients may obtain the Graphviz 15.1.1 source at:",
        "https://gitlab.com/api/v4/projects/4207231/packages/generic/graphviz-releases/15.1.1/graphviz-15.1.1.tar.gz",
        "Viz.js backend build sources:",
        f"https://github.com/mdaines/viz-js/tree/{VIZ_COMMIT}/packages/viz/backend",
        "Expat source: https://github.com/libexpat/libexpat/releases/download/R_2_8_1/expat-2.8.1.tar.gz",
        "Emscripten source: https://github.com/emscripten-core/emscripten/tree/5.0.5",
        "The EPL rights of Graphviz recipients remain applicable independently of AutoYou's license.",
        "",
        "Font Awesome Free SVG icons retain their Fonticons Inc. attribution and CC-BY-4.0 terms.",
        "Their npm notice includes icon, font, and code terms; this inventory retains that notice in full.",
        "DOMPurify is used under the Apache-2.0 alternative of its dual license; its original notices are retained.",
        "",
    ]
    for item in results:
        sections.extend(["=" * 72, f"{item['name']} {item['version']} - {item['licenseExpression']}", item["tarball"], ""])
        for notice in item["notices"]:
            sections.extend([notice["path"], notice["text"], ""])
    for title, url in SUPPLEMENTAL.items():
        sections.extend(["=" * 72, title, url, "", fetch(url).decode("utf-8"), ""])
    # Normalize formatting in the assembled notice without changing its terms.
    assembled = "\n".join(line.rstrip() for line in "\n".join(sections).splitlines()).rstrip() + "\n"
    (output_dir / "google-adk-web-THIRD-PARTY-NOTICES.txt").write_text(assembled, encoding="utf-8")
    print(f"Collected {len(results)} dependency notice entries and {len(SUPPLEMENTAL)} supplemental notices.")


if __name__ == "__main__":
    main()
