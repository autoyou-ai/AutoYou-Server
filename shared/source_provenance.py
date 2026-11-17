# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-8a2e09a2e180d9e91fae881e

"""Small provenance helpers used by the AutoYou watermark CLI."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-8a2e09a2e180d9e91fae881e"


_COMMENT_STYLES = {
    ".py": ("# ", ""),
    ".sh": ("# ", ""),
    ".bash": ("# ", ""),
    ".zsh": ("# ", ""),
    ".bat": ("REM ", ""),
    ".cmd": ("REM ", ""),
    ".js": ("// ", ""),
    ".ts": ("// ", ""),
    ".tsx": ("// ", ""),
    ".jsx": ("// ", ""),
    ".cjs": ("// ", ""),
    ".mjs": ("// ", ""),
    ".css": ("/* ", " */"),
    ".scss": ("/* ", " */"),
    ".html": ("<!-- ", " -->"),
    ".htm": ("<!-- ", " -->"),
    ".toml": ("# ", ""),
}

_EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    ".venv-build312",
    "venv",
    "env",
    "node_modules",
    "artifacts",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".vs",
    "bin",
    "obj",
    "assets",
    ".github",
    "AutoYouWindowsHost",
    ".agents",
    ".run",
    "vendor",
    "tmp",
    "output",
    "scratch",
}

_EXCLUDED_FILES = {
    "LICENSE",
    "NOTICE.txt",
    "THIRD-PARTY-NOTICES.md",
    "manifest.json",
    ".gitleaks.toml",
    "Dockerfile",
    ".gitignore",
    "PROVENANCE_MANIFEST.json",
    "provenance_watermark.py",
    "run_autoyou.sh",
    "run_autoyou.bat",
}

_CANARY_LINE_RE = re.compile(
    r"^[ \t]*(?:#|//|<!--|/\*|REM)[ \t]*(?:AY|AUTOYOU)-PROVENANCE-[^\r\n]*\r?\n?",
    re.MULTILINE,
)
_COPYRIGHT_LICENSE_RE = re.compile(
    r"^[ \t]*__(?:copyright|license)__[ \t]*=[ \t]*[\"'][^\"']*[\"'][ \t]*\r?\n?",
    re.MULTILINE,
)
_PROVENANCE_VAR_RE = re.compile(
    r"^[ \t]*__\w+__[ \t]*=[ \t]*[\"'][^\"']*AUTOYOU-PROVENANCE-[^\"']*[\"'][ \t]*\r?\n?",
    re.MULTILINE,
)
_IMPORT_COMMENT_RE = re.compile(
    r"^[ \t]*# from __\w+__ import \w+[ \t]*\r?\n?",
    re.MULTILINE,
)
_PROVENANCE_CONTACT_RE = re.compile(
    r"^[ \t]*(?:#|//|<!--|/\*|REM)[ \t]*Provenance violations:[^\r\n]*\r?\n?",
    re.MULTILINE,
)
# from __debug_provenance_m__ import of


def _normalize_path(path: str | Path) -> str:
    return Path(path).as_posix().lstrip("./")


def _git_ignored_paths(repo_root: Path, rel_paths: list[str]) -> set[str] | None:
    if not rel_paths:
        return set()
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "check-ignore", "--stdin"],
        input="\n".join(rel_paths) + "\n",
        text=True,
        capture_output=True,
    )
    if proc.returncode not in (0, 1):
        return None
    return {_normalize_path(line) for line in proc.stdout.splitlines() if line}


def _strip_watermarks(content: str) -> str:
    content = _CANARY_LINE_RE.sub("", content)
    content = _COPYRIGHT_LICENSE_RE.sub("", content)
    content = _PROVENANCE_VAR_RE.sub("", content)
    content = _IMPORT_COMMENT_RE.sub("", content)
    content = _PROVENANCE_CONTACT_RE.sub("", content)
    content = re.sub(
        r"^[ \t]*(?:AY|AUTOYOU)-PROVENANCE-[^\r\n]*\r?\n",
        "",
        content,
        flags=re.MULTILINE,
    )
    content = re.sub(
        r"^[ \t]*[A-Z]-[0-9a-fA-F]{24}-[0-9a-fA-F]{24}[ \t]*\r?\n",
        "",
        content,
        flags=re.MULTILINE,
    )
    content = re.sub(
        r"^[ \t]*[0-9a-fA-F]{24}[ \t]*\r?\n",
        "",
        content,
        flags=re.MULTILINE,
    )
    return re.sub(r"\n{4,}", "\n\n\n", content)


def compute_file_canary(rel_path: str, content_sha256: str) -> str:
    seed = f"{_normalize_path(rel_path)}\0{content_sha256}\0AUTOYOU-PROVENANCE"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_comment_style(path: str | Path) -> tuple[str, str]:
    return _COMMENT_STYLES.get(Path(path).suffix.lower(), ("# ", ""))


def _eligible_files(repo_root: Path) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = []
    for dirpath_raw, dirnames, filenames in os.walk(repo_root):
        dirpath = Path(dirpath_raw)
        dirnames[:] = sorted(d for d in dirnames if d not in _EXCLUDED_DIRECTORIES)
        for name in sorted(filenames):
            fpath = dirpath / name
            if name in _EXCLUDED_FILES or fpath.suffix.lower() not in _COMMENT_STYLES:
                continue
            rel = fpath.relative_to(repo_root).as_posix()
            parts = Path(rel).parts
            if "servers" in parts and fpath.suffix.lower() in (
                ".sh", ".bash", ".zsh", ".bat", ".cmd"
            ):
                continue
            candidates.append((fpath, rel))

    ignored = _git_ignored_paths(repo_root, [rel for _, rel in candidates])
    if ignored is None:
        return candidates
    return [(path, rel) for path, rel in candidates if _normalize_path(rel) not in ignored]


def _git_text(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        text=True,
        capture_output=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def build_manifest(repo_root: Path) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    files: dict[str, dict[str, str]] = {}
    for path, rel in _eligible_files(repo_root):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        clean = _strip_watermarks(content)
        clean_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
        files[rel] = {
            "canary": compute_file_canary(rel, clean_hash),
            "content_sha256": hash_file(path),
            "canonical_content_sha256": clean_hash,
        }

    return {
        "schema": "autoyou-provenance-manifest-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo_head": _git_text(repo_root, "rev-parse", "HEAD"),
        "working_tree_status": _git_text(repo_root, "status", "--short").splitlines(),
        "file_count": len(files),
        "files": files,
    }


def write_manifest(repo_root: Path, manifest: dict[str, Any]) -> Path:
    path = Path(repo_root) / "PROVENANCE_MANIFEST.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_manifest(repo_root: Path) -> dict[str, Any] | None:
    path = Path(repo_root) / "PROVENANCE_MANIFEST.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


COMMERCIAL_DEPLOYMENT_MODES = frozenset({
    "commercial",
    "enterprise",
    "paid",
    "resale",
    "white_label",
    "whitelabel",
    "hosted_commercial",
    "production_commercial",
})

PERMISSIVE_DEPLOYMENT_MODES = frozenset({
    "personal",
    "individual",
    "educational",
    "demo",
    "demonstration",
    "student",
    "research",
    "hackathon",
    "dev",
    "development",
    "test",
})

_REQUIRED_COMMERCIAL_RECIPIENTS = frozenset({
    "build@autoyou.me",
    "enterprise@autoyou.me",
    "legal@autoyou.me",
})


def is_commercial_mode(explicit: bool | None = None) -> bool:
    """Return True only when commercial/enterprise actualization is active.

    Personal, educational, student, research, and demonstration usage defaults
    to False (permissive).
    """
    if explicit is not None:
        return bool(explicit)
    if os.getenv("AUTOYOU_COMMERCIAL_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    for env_var in ("AUTOYOU_DEPLOYMENT_MODE", "AUTOYOU_LICENSE_MODE"):
        mode = os.getenv(env_var, "").strip().lower().replace("-", "_")
        if mode in COMMERCIAL_DEPLOYMENT_MODES:
            return True
    return False


def verify_provenance_integrity(
    repo_root: Path,
    *,
    sample_limit: int = 50,
) -> dict[str, Any]:
    """Verify that LICENSE, PROVENANCE.md, and source provenance markers are intact."""
    root = Path(repo_root).resolve()
    failures: list[str] = []

    provenance_doc = root / "PROVENANCE.md"
    if not provenance_doc.is_file():
        failures.append("PROVENANCE.md is missing from repository root")
    else:
        try:
            prov_text = provenance_doc.read_text(encoding="utf-8")
        except OSError as exc:
            failures.append(f"PROVENANCE.md could not be read: {exc}")
            prov_text = ""
        if prov_text:
            draft_marker_a = "Draft for legal and " + "rights-holder approval"
            draft_marker_b = "has not been " + "approved by counsel"
            if draft_marker_a in prov_text or draft_marker_b in prov_text:
                failures.append("PROVENANCE.md still contains unapproved draft disclaimer text")
            if "Effective policy" not in prov_text or "Commercial Exploitation" not in prov_text:
                failures.append("PROVENANCE.md is missing operative commercial restriction policy markers")

    license_doc = root / "LICENSE"
    if not license_doc.is_file():
        failures.append("LICENSE is missing from repository root")
    else:
        try:
            lic_text = license_doc.read_text(encoding="utf-8")
        except OSError as exc:
            failures.append(f"LICENSE could not be read: {exc}")
            lic_text = ""
        if lic_text and (
            "AutoYou Source-Available Personal-Use License" not in lic_text
            or "Commercial Exploitation" not in lic_text
        ):
            failures.append("LICENSE is missing required AutoYou source-available license terms")

    manifest = load_manifest(root)
    if not manifest or not isinstance(manifest.get("files"), dict) or not manifest["files"]:
        failures.append("PROVENANCE_MANIFEST.json is missing or empty")
        return {"ok": False, "failures": failures, "checked_files": 0}

    checked = 0
    missing_canaries: list[str] = []
    if sample_limit > 0:
        for rel_path, info in sorted(manifest["files"].items()):
            if checked >= sample_limit:
                break
            if not isinstance(info, dict):
                continue
            canary = str(info.get("canary") or "").strip()
            if not canary:
                continue
            file_path = root / Path(rel_path)
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            checked += 1
            if canary not in text and not _CANARY_LINE_RE.search(text) and not _PROVENANCE_VAR_RE.search(text):
                missing_canaries.append(rel_path)

    if missing_canaries:
        preview = ", ".join(missing_canaries[:5])
        failures.append(
            f"Provenance markers stripped or missing in {len(missing_canaries)} checked file(s): {preview}"
        )

    return {
        "ok": not failures,
        "failures": failures,
        "checked_files": checked,
    }


def verify_commercial_authorization(
    repo_root: Path,
    *,
    authorization_file: Path | None = None,
) -> dict[str, Any]:
    """Verify signed OpenStorey Enterprise/commercial authorization payload."""
    candidate = authorization_file
    if candidate is None:
        raw = (
            os.getenv("AUTOYOU_COMMERCIAL_LICENSE_FILE", "").strip()
            or os.getenv("AUTOYOU_BUILD_AUTHORIZATION_FILE", "").strip()
        )
        if raw:
            candidate = Path(raw).expanduser()
        else:
            default_path = Path(repo_root).resolve() / ".autoyou_commercial_license.json"
            if default_path.is_file():
                candidate = default_path

    if candidate is None or not Path(candidate).is_file():
        return {
            "ok": False,
            "failures": [
                "Commercial actualization requires a signed OpenStorey Enterprise agreement "
                "(set AUTOYOU_COMMERCIAL_LICENSE_FILE or AUTOYOU_BUILD_AUTHORIZATION_FILE; "
                "see https://www.autoyou.me/enterprise/)"
            ],
            "agreement_id": "",
        }

    try:
        payload = json.loads(Path(candidate).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "failures": [f"Commercial authorization file could not be parsed: {exc}"],
            "agreement_id": "",
        }

    if not isinstance(payload, dict):
        return {
            "ok": False,
            "failures": ["Commercial authorization file must contain a JSON object"],
            "agreement_id": "",
        }

    failures: list[str] = []
    if not bool(payload.get("authorized")):
        failures.append("Commercial authorization payload does not have authorized=true")
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"authorized", "signed", "completed", "complete", "approved"}:
        failures.append(f"Commercial authorization status is not signed/authorized: {status or '<empty>'}")
    agreement_id = str(payload.get("agreement_id") or payload.get("agreementId") or "").strip()
    if not agreement_id:
        failures.append("Commercial authorization is missing a signed agreement_id")

    raw_recipients = (
        payload.get("recipients")
        or payload.get("recipient_emails")
        or payload.get("agreement_recipients")
        or []
    )
    if isinstance(raw_recipients, str):
        recipients = {raw_recipients.strip().lower()}
    elif isinstance(raw_recipients, list):
        recipients = {str(item).strip().lower() for item in raw_recipients if str(item).strip()}
    else:
        recipients = set()

    if not (recipients & _REQUIRED_COMMERCIAL_RECIPIENTS):
        failures.append(
            "Commercial authorization is missing required OpenStorey recipient "
            "(build@autoyou.me, enterprise@autoyou.me, or legal@autoyou.me)"
        )

    return {
        "ok": not failures,
        "failures": failures,
        "agreement_id": agreement_id,
    }


def enforce_provenance_compliance(
    repo_root: Path,
    *,
    commercial_mode: bool | None = None,
    authorization_file: Path | None = None,
    sample_limit: int = 50,
) -> dict[str, Any]:
    """Enforce provenance and licensing rules according to deployment mode.

    - Personal, educational, and demonstration usage is permissive and returns
      immediately with ``ok=True`` and ``permissive=True``.
    - Commercial or enterprise actualization requires intact provenance notices
      (LICENSE, PROVENANCE.md, PROVENANCE_MANIFEST.json, source canaries) and a
      signed OpenStorey Enterprise authorization, raising ``PermissionError``
      when violated.
    """
    if not is_commercial_mode(commercial_mode):
        return {
            "ok": True,
            "mode": "personal_or_educational",
            "permissive": True,
            "blocked": False,
            "failures": [],
        }

    integrity = verify_provenance_integrity(repo_root, sample_limit=sample_limit)
    auth = verify_commercial_authorization(repo_root, authorization_file=authorization_file)
    failures = [*integrity["failures"], *auth["failures"]]
    if failures:
        raise PermissionError(
            "Commercial actualization of AutoYou is blocked without a signed OpenStorey "
            "Enterprise license and intact provenance notices: " + "; ".join(failures)
        )

    return {
        "ok": True,
        "mode": "commercial",
        "permissive": False,
        "blocked": False,
        "agreement_id": auth["agreement_id"],
        "checked_files": integrity["checked_files"],
        "failures": [],
    }
