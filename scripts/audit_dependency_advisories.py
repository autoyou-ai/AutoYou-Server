#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-J-7920636f6e74726962757469-cd04d6fc806f02711b5ea532

"""Query PyPI release advisories without installing or importing dependencies.

This is a manifest check, not a dependency resolver or malware scanner.
Only public package names and versions are sent to PyPI. Reports identify
skipped sources and errors; neither is treated as a clean result.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request

__debug_trace_j__ = "AUTOYOU-PROVENANCE-J-7920636f6e74726962757469-cd04d6fc806f02711b5ea532"


REPO_ROOT = Path(__file__).resolve().parents[1]
DECISIONS = REPO_ROOT / "requirements" / "advisory-decisions.json"
RECORD = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*(==|>=)\s*([A-Za-z0-9_.+!-]+)")
# from __debug_trace_j__ import autoyou_provenance_j_7920636f6e74726962757469_cd04d6fc806f02711b5ea532


def inventory(paths: list[Path], include_minimums: bool) -> tuple[list[dict], list[dict]]:
    records: dict[tuple[str, str], dict] = {}
    skipped = []
    for path in paths:
        for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith(("#", "--hash=", "-r ", "-c ")):
                continue
            match = RECORD.match(line)
            source = {"file": path.relative_to(REPO_ROOT).as_posix(), "line": number}
            if not match or (match[2] != "==" and not include_minimums):
                skipped.append({**source, "requirement": line, "reason": "not an audited exact release"})
                continue
            name = re.sub(r"[-_.]+", "-", match[1]).lower()
            key = (name, match[3])
            entry = records.setdefault(key, {"name": name, "version": match[3], "sources": []})
            entry["sources"].append({**source, "kind": "pin" if match[2] == "==" else "minimum"})
    return sorted(records.values(), key=lambda item: (item["name"], item["version"])), skipped


def query_release(record: dict) -> dict:
    name = urllib.parse.quote(record["name"], safe="")
    version = urllib.parse.quote(record["version"], safe="")
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "AutoYou-manifest-advisory-check/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.load(response)
        if (not isinstance(data, dict) or not isinstance(data.get("info"), dict)
                or not isinstance(data.get("vulnerabilities"), list)
                or any(not isinstance(item, dict) for item in data["vulnerabilities"])):
            raise ValueError("Unexpected PyPI response schema; advisory status was not established")
        # Keep only public advisory fields, not arbitrary package metadata.
        advisories = [{key: advisory.get(key) for key in
                       ("id", "aliases", "summary", "details", "fixed_in", "link", "withdrawn")}
                      for advisory in data.get("vulnerabilities", [])
                      if not advisory.get("withdrawn")]
        return {**record, "url": url, "status": "checked", "advisories": advisories,
                "yanked": bool(data.get("info", {}).get("yanked"))}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return {**record, "url": url, "status": "error", "error": str(exc)}


def load_decisions(path: Path) -> list[dict]:
    """Read explicit reviewed advisory decisions; a malformed file is an error, not a pass."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    decisions = data.get("decisions") if isinstance(data, dict) else None
    required = ("package", "versions", "advisory_ids", "manifests", "justification", "detail", "reviewed_at", "review_by")
    if not isinstance(decisions, list) or any(
            not isinstance(item, dict) or any(not item.get(key) for key in required) for item in decisions):
        raise ValueError(f"{path.name} must list complete decisions with {', '.join(required)}")
    return decisions


def apply_decisions(results: list[dict], decisions: list[dict], today: str) -> None:
    """Move advisories covered by an unexpired, exactly scoped decision to ``reviewed_advisories``.

    A decision covers one package, its listed declared versions and manifests,
    and the listed advisory ids or aliases. Expired decisions stop applying.
    """
    for entry in results:
        open_advisories, reviewed = [], []
        files = {source["file"] for source in entry.get("sources", [])}
        for advisory in entry.get("advisories") or []:
            ids = {advisory.get("id"), *(advisory.get("aliases") or [])}
            decision = next((item for item in decisions
                             if re.sub(r"[-_.]+", "-", item["package"]).lower() == entry["name"]
                             and entry["version"] in item["versions"]
                             and files and files <= set(item["manifests"])
                             and ids & set(item["advisory_ids"])
                             and str(item["review_by"]) >= today), None)
            if decision is None:
                open_advisories.append(advisory)
            else:
                reviewed.append({**advisory, "decision": {key: decision[key] for key in
                                 ("justification", "detail", "reviewed_at", "review_by")}})
        if "advisories" in entry:
            entry["advisories"] = open_advisories
        if reviewed:
            entry["reviewed_advisories"] = reviewed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-manifests", action="store_true", help="Check exact pins in every requirements/*.txt file.")
    parser.add_argument("--include-minimums", action="store_true", help="Also check declared >= floors; this does not resolve ranges.")
    parser.add_argument("--output", required=True, type=Path, help="JSON report destination outside live configuration.")
    args = parser.parse_args()
    paths = sorted(path for path in (REPO_ROOT / "requirements").glob("*.txt")
                   if not path.name.startswith(".")) if args.all_manifests else [REPO_ROOT / "requirements/locked.txt"]
    records, skipped = inventory(paths, args.include_minimums)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(query_release, records))
    now = datetime.now(timezone.utc)
    apply_decisions(results, load_decisions(DECISIONS), now.date().isoformat())
    vulnerable = [entry for entry in results if entry.get("advisories")]
    reviewed = [entry for entry in results if entry.get("reviewed_advisories")]
    errors = [entry for entry in results if entry["status"] == "error"]
    report = {
        "schema_version": 1, "checked_at": now.isoformat(),
        "source": "PyPI release JSON API (known advisories)",
        "scope": "Declared exact releases and optionally minimum versions; not a resolved environment",
        "limitations": ["No malware or source-code analysis", "No transitive resolution beyond listed pins",
                        "All markers are inspected without claiming platform compatibility",
                        "Unpinned ranges and VCS sources need separate review", "Empty advisory lists are not a safety guarantee"],
        "manifests": [{"path": path.relative_to(REPO_ROOT).as_posix(),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths],
        "counts": {"queried": len(results), "with_advisories": len(vulnerable), "reviewed_decisions": len(reviewed),
                   "errors": len(errors), "skipped": len(skipped)},
        "results": results, "skipped": skipped,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"]))
    return 2 if errors else (1 if vulnerable else 0)


if __name__ == "__main__":
    raise SystemExit(main())
