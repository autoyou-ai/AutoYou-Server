#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Verify SignToROSS/OpenSign build authorization before official builds."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_REQUIRED_BUILD_RECIPIENT = "build@autoyou.me"
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
DEFAULT_AUTHORIZATION_DIRS = (
    Path(__file__).resolve().parent.parent / "docs" / "legal" / "drafts" / "build-authorizations",
    Path.home() / ".autoyou" / "build-authorizations",
    Path.home() / "Downloads",
    Path.home(),
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return tuple(value.strip() for value in raw_value.split(",") if value.strip())


def _load_json_file(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Could not read build authorization file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Build authorization file must contain a JSON object: {path}")
    return payload


def _authorization_search_dirs() -> tuple[Path, ...]:
    configured_dirs = os.getenv("AUTOYOU_BUILD_AUTHORIZATION_DIRS", "").strip()
    if configured_dirs:
        return tuple(Path(value).expanduser() for value in configured_dirs.split(os.pathsep) if value.strip())
    configured_dir = os.getenv("AUTOYOU_BUILD_AUTHORIZATION_DIR", "").strip()
    if configured_dir:
        return (Path(configured_dir).expanduser(), *DEFAULT_AUTHORIZATION_DIRS)
    return DEFAULT_AUTHORIZATION_DIRS


def _authorization_file_candidates(artifact_profile: str) -> tuple[str, ...]:
    profile = str(artifact_profile or "").strip()
    if not profile:
        return ("build-authorization.json",)
    return (
        f"autoyou-build-authorization-{profile}.json",
        f"official-build-authorization-{profile}.json",
        f"{profile}-build-authorization.json",
        f"{profile}.build-authorization.json",
    )


def _discover_authorization_file(artifact_profile: str) -> Path | None:
    for directory in _authorization_search_dirs():
        for filename in _authorization_file_candidates(artifact_profile):
            candidate = directory / filename
            if candidate.is_file():
                return candidate
    return None


def _fetch_authorization(server_url: str, authorization_id: str, token: str, timeout: float) -> dict[str, object]:
    effective_url = (server_url or os.getenv("AUTOYOU_BUILD_AUTHORIZATION_URL", "https://sign.autoyou.me")).strip()
    if not effective_url:
        raise RuntimeError("AUTOYOU_BUILD_AUTHORIZATION_URL or --server-url is required.")
    effective_token = (token or os.getenv("AUTOYOU_BUILD_AUTH_CHECK_TOKEN", "")).strip()
    url = effective_url.rstrip("/") + "/v1/internal/build-authorizations/" + quote(authorization_id, safe="")
    headers = {"User-Agent": "AutoYouOfficialBuildGate/1.0"}
    if effective_token:
        headers["Authorization"] = f"Bearer {effective_token}"
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Build authorization check returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Build authorization service could not be reached: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Build authorization service returned a non-object payload.")
    return payload


def _emails_from(value: object) -> set[str]:
    if isinstance(value, str):
        return {match.group(0).lower() for match in EMAIL_PATTERN.finditer(value)}
    if isinstance(value, dict):
        emails: set[str] = set()
        for child in value.values():
            emails.update(_emails_from(child))
        return emails
    if isinstance(value, (list, tuple, set)):
        emails: set[str] = set()
        for child in value:
            emails.update(_emails_from(child))
        return emails
    return set()


def _payload_recipients(payload: dict[str, object]) -> set[str]:
    recipients: set[str] = set()
    for key in (
        "recipients",
        "recipient_emails",
        "recipientEmails",
        "requested_recipients",
        "requestedRecipients",
        "agreement_recipients",
        "agreementRecipients",
    ):
        recipients.update(_emails_from(payload.get(key)))
    return recipients


def _payload_signer_email(payload: dict[str, object]) -> str:
    for key in ("signer_email", "signerEmail", "oauth_email", "oauthEmail"):
        emails = sorted(_emails_from(payload.get(key)))
        if emails:
            return emails[0]
    return ""


def _validate_authorization(
    payload: dict[str, object],
    artifact_profile: str,
    *,
    required_recipients: tuple[str, ...],
    expected_oauth_email: str = "",
) -> None:
    if not bool(payload.get("authorized")):
        raise RuntimeError("Build authorization is not signed/authorized yet.")
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"authorized", "signed", "completed", "complete", "approved"}:
        raise RuntimeError(f"Build authorization status is not authorized: {status or '<empty>'}")
    expected_profile = str(artifact_profile or "").strip()
    actual_profile = str(payload.get("artifact_profile") or payload.get("artifactProfile") or "").strip()
    if expected_profile and actual_profile and actual_profile != expected_profile:
        raise RuntimeError(
            f"Build authorization artifact profile mismatch: expected {expected_profile!r}, got {actual_profile!r}."
        )
    if not str(payload.get("agreement_id") or payload.get("agreementId") or "").strip():
        raise RuntimeError("Build authorization is missing the signed agreement id.")
    recipients = _payload_recipients(payload)
    for required_recipient in required_recipients:
        normalized = required_recipient.strip().lower()
        if normalized and normalized not in recipients:
            raise RuntimeError(f"Build authorization is missing required agreement recipient: {normalized}")
    signer_email = _payload_signer_email(payload)
    if not signer_email:
        raise RuntimeError("Build authorization is missing the OAuth signer email.")
    expected_signer = expected_oauth_email.strip().lower()
    if expected_signer and signer_email != expected_signer:
        raise RuntimeError(f"Build authorization signer mismatch: expected {expected_signer!r}, got {signer_email!r}.")
    if signer_email not in recipients:
        raise RuntimeError("Build authorization OAuth signer email is not listed as an agreement recipient.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-profile", default=os.getenv("AUTOYOU_BUILD_ARTIFACT_PROFILE", ""))
    parser.add_argument("--authorization-id", default=os.getenv("AUTOYOU_BUILD_AUTHORIZATION_ID", ""))
    parser.add_argument("--authorization-file", type=Path, default=os.getenv("AUTOYOU_BUILD_AUTHORIZATION_FILE") or None)
    parser.add_argument("--server-url", default=os.getenv("AUTOYOU_BUILD_AUTHORIZATION_URL", "https://sign.autoyou.me"))
    parser.add_argument("--token", default=os.getenv("AUTOYOU_BUILD_AUTH_CHECK_TOKEN", ""))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("AUTOYOU_BUILD_AUTH_TIMEOUT_SECONDS", "15")))
    parser.add_argument(
        "--required-recipient",
        action="append",
        default=None,
        help=(
            "Agreement recipient that must be present in the signed authorization payload. "
            "Defaults to build@autoyou.me; repeat for additional recipients."
        ),
    )
    parser.add_argument(
        "--expected-oauth-email",
        default=os.getenv("AUTOYOU_BUILD_AUTH_OAUTH_EMAIL", ""),
        help="Optional OAuth signer email expected for this build authorization.",
    )
    parser.add_argument(
        "--required",
        action="store_true",
        default=_env_bool("AUTOYOU_OFFICIAL_BUILD_AUTH_REQUIRED", False),
        help="Fail closed when authorization is missing. Also enabled by AUTOYOU_OFFICIAL_BUILD_AUTH_REQUIRED=true.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.required and not args.authorization_id and not args.authorization_file:
        print("OK: official build authorization not required for this build.")
        return 0
    if not args.authorization_id and not args.authorization_file:
        args.authorization_file = _discover_authorization_file(args.artifact_profile)
    if not args.authorization_id and not args.authorization_file:
        print("FAIL: official build authorization is required but no authorization id/file was provided.", file=sys.stderr)
        return 1
    try:
        required_recipients = tuple(args.required_recipient or _env_csv("AUTOYOU_BUILD_AUTH_REQUIRED_RECIPIENTS", (DEFAULT_REQUIRED_BUILD_RECIPIENT,)))
        payload = (
            _load_json_file(args.authorization_file)
            if args.authorization_file
            else _fetch_authorization(args.server_url, args.authorization_id, args.token, args.timeout)
        )
        _validate_authorization(
            payload,
            args.artifact_profile,
            required_recipients=required_recipients,
            expected_oauth_email=args.expected_oauth_email,
        )
    except RuntimeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("OK: official build authorization is signed and matches this artifact profile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
