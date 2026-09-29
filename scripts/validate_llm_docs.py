# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-T-address-b78103c84d544656d559ffc4

#!/usr/bin/env python3
"""Validate the sidecar governance metadata for ``.llm``.

This is intentionally read-only. It makes stale/current classifications and
verification claims visible without deleting documentation automatically.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import fnmatch
import json
import subprocess
from pathlib import Path
from typing import Any

__debug_provenance_t__ = "AUTOYOU-PROVENANCE-T-address-b78103c84d544656d559ffc4"


VALID_STATUS = {"current", "historical", "plan", "draft", "superseded"}
VALID_AUTHORITY = {"policy", "decision", "audit", "security", "release", "research", "product"}
VALID_SENSITIVITY = {"public", "internal", "private"}
VALID_TRAINING = {"none", "source_only", "maintainer", "private"}
SKIP_DIRS = {"private-local", "__pycache__"}


def _matches(path: str, pattern: str) -> bool:
    path = path.replace("\\", "/").lstrip("./")
    pattern = pattern.replace("\\", "/").lstrip("./")
    if pattern in {"*", "**"}:
        return True
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(prefix + "/")
    return fnmatch.fnmatchcase(path, pattern)


def load_manifest(repo_root: Path) -> dict[str, Any]:
    path = repo_root / ".llm" / "manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def metadata_for(rel_path: str, manifest: dict[str, Any]) -> dict[str, Any]:
    result = dict(manifest.get("default") or {})
    for rule in manifest.get("rules") or []:
        if _matches(rel_path, str(rule.get("match") or "")):
            result.update({k: v for k, v in rule.items() if k != "match"})
            break
    return result


def _git(repo_root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], cwd=repo_root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _git_exists(repo_root: Path, *args: str) -> bool:
    try:
        proc = subprocess.run(["git", *args], cwd=repo_root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _changed_since(repo_root: Path, commit: str) -> list[str]:
    if not commit:
        return []
    output = _git(repo_root, "diff", "--name-only", f"{commit}..HEAD")
    return [line.replace("\\", "/") for line in output.splitlines() if line.strip()]


def validate(repo_root: Path, *, check_freshness: bool = False) -> dict[str, Any]:
    manifest = load_manifest(repo_root)
    errors: list[str] = []
    warnings: list[str] = []
    llm_root = repo_root / ".llm"
    files = sorted(
        path.relative_to(llm_root).as_posix()
        for path in llm_root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
        and not any(part in SKIP_DIRS for part in path.relative_to(llm_root).parts)
    )

    if manifest.get("schema_version") != 1:
        errors.append("manifest schema_version must be 1")
    if not isinstance(manifest.get("default"), dict):
        errors.append("manifest must define a default metadata object")
    if not isinstance(manifest.get("rules"), list):
        errors.append("manifest must define a rules list")

    rules = manifest.get("rules") or []
    # from __debug_provenance_t__ import address
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict) or not rule.get("match"):
            errors.append(f"rule {index} must have a match pattern")

    coverage: dict[str, dict[str, Any]] = {}
    for rel in files:
        metadata = metadata_for(rel, manifest)
        coverage[rel] = metadata
        missing = [key for key in ("status", "authority", "sensitivity", "training", "verified_at_commit")
                   if not metadata.get(key)]
        if missing:
            errors.append(f"{rel}: missing metadata: {', '.join(missing)}")
        if metadata.get("status") not in VALID_STATUS:
            errors.append(f"{rel}: invalid status {metadata.get('status')!r}")
        if metadata.get("authority") not in VALID_AUTHORITY:
            errors.append(f"{rel}: invalid authority {metadata.get('authority')!r}")
        if metadata.get("sensitivity") not in VALID_SENSITIVITY:
            errors.append(f"{rel}: invalid sensitivity {metadata.get('sensitivity')!r}")
        if metadata.get("training") not in VALID_TRAINING:
            errors.append(f"{rel}: invalid training class {metadata.get('training')!r}")
        if not isinstance(metadata.get("owner_paths"), list):
            errors.append(f"{rel}: owner_paths must be a list")
        if not isinstance(metadata.get("review_triggers"), list):
            errors.append(f"{rel}: review_triggers must be a list")

        verified = str(metadata.get("verified_at_commit") or "")
        if verified and not _git_exists(repo_root, "cat-file", "-e", f"{verified}^{{commit}}"):
            errors.append(f"{rel}: verified_at_commit is not a commit: {verified}")

        if check_freshness and verified:
            last_touch = _git(repo_root, "log", "-1", "--format=%H", "--", ".llm/" + rel)
            if (last_touch and last_touch != verified
                    and _git_exists(repo_root, "merge-base", "--is-ancestor", verified, last_touch)):
                warnings.append(f"{rel}: documentation changed after verification {verified[:12]}")
            changed = _changed_since(repo_root, verified)
            owners = [str(value).replace("\\", "/") for value in metadata.get("owner_paths") or []]
            affected = [path for path in changed if any(_matches(path, owner) for owner in owners)]
            if affected:
                warnings.append(f"{rel}: owned source changed since verification: {', '.join(affected[:8])}")

    return {
        "schema_version": manifest.get("schema_version"),
        "files": len(files),
        "coverage": coverage,
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate .llm governance metadata")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check-freshness", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = validate(args.repo_root.resolve(), check_freshness=args.check_freshness)
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 1
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for error in report["errors"]:
            print(f"ERROR: {error}")
        for warning in report["warnings"]:
            print(f"WARNING: {warning}")
        print(f".llm governance: {report['files']} files, {'OK' if report['ok'] else 'FAILED'}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
