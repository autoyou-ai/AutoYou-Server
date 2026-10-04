#!/usr/bin/env python3
"""Query PyPI release advisories without installing or importing dependencies.

This is a manifest check, not a dependency resolver or malware scanner.
Only public package names and versions are sent to PyPI. Reports identify
skipped sources and errors; neither is treated as a clean result.
"""

from __future__ import annotations

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


REPO_ROOT = Path(__file__).resolve().parents[1]
RECORD = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*(==|>=)\s*([A-Za-z0-9_.+!-]+)")


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
    vulnerable = [entry for entry in results if entry.get("advisories")]
    errors = [entry for entry in results if entry["status"] == "error"]
    report = {
        "schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
        "source": "PyPI release JSON API (known advisories)",
        "scope": "Declared exact releases and optionally minimum versions; not a resolved environment",
        "limitations": ["No malware or source-code analysis", "No transitive resolution beyond listed pins",
                        "All markers are inspected without claiming platform compatibility",
                        "Unpinned ranges and VCS sources need separate review", "Empty advisory lists are not a safety guarantee"],
        "manifests": [{"path": path.relative_to(REPO_ROOT).as_posix(),
                       "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths],
        "counts": {"queried": len(results), "with_advisories": len(vulnerable), "errors": len(errors), "skipped": len(skipped)},
        "results": results, "skipped": skipped,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"]))
    return 2 if errors else (1 if vulnerable else 0)


if __name__ == "__main__":
    raise SystemExit(main())
