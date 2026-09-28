#!/usr/bin/env python3
"""Produce read-only evidence before pruning a source or documentation file."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def _run(repo_root: Path, *args: str) -> str:
    try:
        proc = subprocess.run([*args], cwd=repo_root, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def audit(repo_root: Path, candidate: str | Path) -> dict:
    rel = str(candidate).replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    path = repo_root / rel
    if not path.exists():
        return {"path": rel, "exists": False, "safe_to_delete": False,
                "reasons": ["candidate does not exist"]}

    references = _run(repo_root, "git", "grep", "-n", "--fixed-string", rel, "--").splitlines()
    basename_refs = _run(repo_root, "git", "grep", "-n", "--fixed-string", path.name, "--").splitlines()
    history = _run(repo_root, "git", "log", "-8", "--oneline", "--follow", "--", rel).splitlines()
    reasons = []
    if references:
        reasons.append("path is referenced by tracked files")
    if basename_refs:
        reasons.append("basename appears in tracked files")
    if rel.startswith(".llm/"):
        reasons.append("documentation requires .llm metadata review before deletion")
    else:
        reasons.append("source deletion requires entrypoint, registry, packaging, and test review")
    if any(token in rel.lower() for token in ("agent", "plugin", "registry", "manifest", "bootstrap", "run_")):
        reasons.append("dynamic runtime or packaging surface may not be fully represented in Graphify")
    return {
        "path": rel,
        "exists": True,
        "safe_to_delete": not references and not basename_refs and rel.startswith(".llm/"),
        "reasons": reasons,
        "references": references[:80],
        "basename_references": basename_refs[:80],
        "recent_history": history,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit pruning candidates without deleting anything")
    parser.add_argument("candidate", nargs="+", help="repository-relative paths")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = [audit(args.repo_root.resolve(), path) for path in args.candidate]
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for item in report:
            print(f"{item['path']}: {'candidate only' if item['safe_to_delete'] else 'manual review required'}")
            for reason in item["reasons"]:
                print(f"  - {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
