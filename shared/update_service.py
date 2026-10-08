# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-V-wallet-aa3a26d3c3d9cd46ceb1e7d1

"""AutoYou software-update service.

Performs a software update when one becomes available, from an **authorized
update server** that publishes a *signed* update manifest. The design has two
install shapes, mirroring how AutoYou actually ships:

* **source install** (``python server.py`` / bootstrap, ``.git`` present and not
  Nuitka-compiled): update == **sync to ``origin/main``** (fetch + fast-forward
  merge), then restart. This is the "software updates to origin main and sync"
  path.
* **packaged install** (Nuitka ``AutoYou.exe`` / ``AutoYou.dist``): download the
  signed, OS-codesigned artifact named in the manifest from the authorized
  server, verify its SHA-256, stage it, and hand off to the OS installer /
  swap-on-restart updater.

Security model (fail-closed):

* The manifest is delivered as a signed **envelope**
  ``{"payload": "<canonical-json>", "signature": "<base64>",
  "algorithm": "ed25519", "key_id": "<id>"}``.
* The Ed25519 signature over ``payload`` (UTF-8 bytes) is verified against a
  **bundled trusted public key**. If no trusted key is configured, or the key id
  is unknown, or the signature fails, the update is rejected - unsigned/untrusted
  manifests are never applied.
* Packaged artifacts are additionally pinned by SHA-256 from the (signed)
  manifest, and the OS verifies the artifact's own Authenticode / Developer-ID +
  notarization signature at install/launch.

This module is intentionally dependency-light and side-effect-free except for
the explicit ``apply_*`` methods, so the pure logic (verify / compare / plan)
is unit-testable without network or a real install.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import base64
import hashlib
import json
import logging
import os
import platform
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from shared.macos_runtime_support import is_app_store_build
from shared.update_trusted_keys import TRUSTED_UPDATE_KEYS
from shared.version import get_release_channel, get_version

__debug_provenance_v__ = "AUTOYOU-PROVENANCE-V-wallet-aa3a26d3c3d9cd46ceb1e7d1"


LOGGER = logging.getLogger(__name__)

DEFAULT_FEED_BASE = "https://app.autoyou.me/v1/updates"
MANIFEST_SCHEMA_VERSION = 1
DEFAULT_PRODUCT = "autoyou-server"
SUPPORTED_CHANNELS = {"stable", "beta", "dev"}
AUTHORIZED_FEED_HOSTS = {"app.autoyou.me"}
MAX_MANIFEST_BYTES = 1024 * 1024
SIMULATED_VERSION_ENV = "AUTOYOU_UPDATE_SIMULATED_CURRENT_VERSION"
FEED_AUTH_MESSAGE = (
    "The AutoYou update service rejected this install's account credential. "
    "Reconnect this AutoYou account, then check for updates again."
)
WINDOWS_STORE_PRODUCT_IDS = {
    "autoyou-server": "9MW8L2WFW7WV",
    "autoyou-connect": "9PFX2JVXSH0X",
}

# Public Ed25519 trust anchors compiled into packaged clients. The release
# operator provisions this generated module with ``sign_update_manifest.py
# bundle-key``; environment values remain available for source/Docker installs.
_BUNDLED_TRUSTED_KEYS: Dict[str, str] = dict(TRUSTED_UPDATE_KEYS)

class UpdateError(RuntimeError):
    """Raised for any update check/verify/apply failure (fail-closed)."""


class UpdateAuthError(UpdateError):
    """Raised when the update feed rejects this install's account credential.

    The feed is OAuth-gated, so a 401/403 means the saved AutoYou account link
    was never completed, was revoked, or has aged out - not that the feed is
    broken. Callers surface a "reconnect your AutoYou account" action instead
    of a generic "update check failed".
    """


def _windows_package_name() -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes

        length = ctypes.c_uint32(0)
        get_name = ctypes.windll.kernel32.GetCurrentPackageFullName
        if get_name(ctypes.byref(length), None) != 122:  # ERROR_INSUFFICIENT_BUFFER
            return ""
        value = ctypes.create_unicode_buffer(length.value)
        return value.value if get_name(ctypes.byref(length), value) == 0 else ""
    except (AttributeError, OSError, ValueError):
        return ""


def native_store_uri(product: str = DEFAULT_PRODUCT) -> str:
    """Resolve the running app's update owner without contacting an update feed."""
    if is_app_store_build():
        return "macappstore://showUpdatesPage"
    if _windows_package_name():
        product_id = WINDOWS_STORE_PRODUCT_IDS.get(product)
        return f"ms-windows-store://pdp/?ProductId={product_id}" if product_id else ""
    return ""


def software_updates_enabled(config_value: Any = True) -> bool:
    """Return the effective opt-in state, with the process override winning."""
    override = os.environ.get("AUTOYOU_SOFTWARE_UPDATES_ENABLED")
    if override is not None and str(override).strip():
        return str(override).strip().lower() in {"1", "true", "yes", "on"}
    return bool(config_value)


def simulated_current_version() -> str:
    """Return the release-QA override for this install's reported version.

    Set ``AUTOYOU_UPDATE_SIMULATED_CURRENT_VERSION`` to an older release to
    exercise the "an update is available" path - the admin panel, the apply
    flow, and the client surfaces - against a real signed feed without cutting
    a throwaway release first.

    This only changes what the install claims to *be*. Every trust control
    still applies unchanged: the manifest signature, the trusted key id, the
    product/channel scope, the pinned git commit, and the artifact SHA-256 and
    size. It can make an update appear, never make an untrusted one install.
    """
    raw = str(os.environ.get(SIMULATED_VERSION_ENV) or "").strip()
    if not raw:
        return ""
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,63}", raw):
        LOGGER.warning(
            "Ignoring %s=%r: not a comparable version string", SIMULATED_VERSION_ENV, raw
        )
        return ""
    LOGGER.warning(
        "Software update checks are reporting the simulated version %s (%s is set)",
        raw,
        SIMULATED_VERSION_ENV,
    )
    return raw


def _running_in_container() -> bool:
    if str(os.environ.get("AUTOYOU_RUNTIME_VARIANT") or "").strip().lower() == "docker":
        return True
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def _running_in_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text(encoding="utf-8").lower()
    except OSError:
        return False

@dataclass
class UpdateArtifact:
    platform_key: str
    url: str
    sha256: str
    size: int

@dataclass
class UpdateManifest:
    schema_version: int
    product: str
    version: str
    channel: str
    notes: str = ""
    released_at: str = ""
    git_remote: str = "origin"
    git_branch: str = "main"
    git_commit: str = ""
    artifacts: Dict[str, UpdateArtifact] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

def _load_trusted_keys() -> Dict[str, Ed25519PublicKey]:
    """Resolve trusted Ed25519 public keys (env overrides + bundled)."""
    raw: Dict[str, str] = dict(_BUNDLED_TRUSTED_KEYS)

    env_multi = os.environ.get("AUTOYOU_UPDATE_PUBLIC_KEYS", "").strip()
    if env_multi:
        try:
            parsed = json.loads(env_multi)
            if isinstance(parsed, dict):
                raw.update({str(k): str(v) for k, v in parsed.items()})
        except (ValueError, TypeError):
            LOGGER.warning("AUTOYOU_UPDATE_PUBLIC_KEYS is not valid JSON; ignoring")

    env_single = os.environ.get("AUTOYOU_UPDATE_PUBLIC_KEY", "").strip()
    if env_single:
        raw[os.environ.get("AUTOYOU_UPDATE_PUBLIC_KEY_ID", "default").strip() or "default"] = env_single

    keys: Dict[str, Ed25519PublicKey] = {}
    for key_id, b64 in raw.items():
        try:
            keys[key_id] = Ed25519PublicKey.from_public_bytes(base64.b64decode(b64))
        except Exception as exc:  # noqa: BLE001 - bad key material is non-fatal, just skipped
            LOGGER.warning("Ignoring malformed trusted update key '%s': %s", key_id, exc)
    return keys

def platform_key() -> str:
    """Return the artifact key for the running OS/arch, e.g. ``windows-x64``."""
    system = platform.system().lower()
    machine = (platform.machine() or "").lower()
    # from __debug_provenance_v__ import wallet
    if machine in {"arm64", "aarch64"}:
        arch = "arm64"
    elif machine in {"x86_64", "amd64", "x64"}:
        arch = "x64"
    else:
        arch = machine or "unknown"
    if system.startswith("win"):
        return f"windows-{arch}"
    if system == "darwin":
        return f"macos-{arch}"
    if _running_in_container():
        return f"docker-{arch}"
    if _running_in_wsl():
        return f"wsl-{arch}"
    return f"{system}-{arch}"

class UpdateService:
    def __init__(
        self,
        *,
        feed_base: Optional[str] = None,
        channel: Optional[str] = None,
        trusted_keys: Optional[Dict[str, Ed25519PublicKey]] = None,
        app_root: Optional[Path] = None,
        http_get: Optional[Callable[[str, float], bytes]] = None,
        auth_token: str = "",
        cookie_header: str = "",
        enabled: Any = True,
        product: Optional[str] = None,
        current_version: Optional[str] = None,
        launcher: Optional[Callable[[Path], None]] = None,
    ) -> None:
        self._product = str(product or os.environ.get("AUTOYOU_UPDATE_PRODUCT") or DEFAULT_PRODUCT).strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", self._product):
            raise UpdateError(f"Invalid update product id: {self._product!r}")
        if feed_base is not None:
            self._feed_base = str(feed_base).rstrip("/")
        else:
            feed_root = os.environ.get("AUTOYOU_UPDATE_FEED_BASE", "").strip() or DEFAULT_FEED_BASE
            self._feed_base = f"{feed_root.rstrip('/')}/{self._product}"
        self._channel = str(channel or get_release_channel()).strip().lower()
        if self._channel not in SUPPORTED_CHANNELS:
            raise UpdateError(f"Invalid update channel: {self._channel!r}")
        self._trusted_keys = trusted_keys if trusted_keys is not None else _load_trusted_keys()
        self._app_root = Path(app_root) if app_root else self._resolve_app_root()
        self._http_get = http_get or self._default_http_get
        self._auth_token = str(auth_token or "").strip()
        self._cookie_header = str(cookie_header or "").strip()
        self._enabled = software_updates_enabled(enabled)
        self._current_version = str(current_version or "").strip()
        self._launcher = launcher
        # When using the real HTTP getter, artifact downloads stream to disk
        # (installers can be large); a caller-injected getter (tests) is buffered.
        self._http_get_is_default = http_get is None

    # ── identity / environment ────────────────────────────────────────────
    @staticmethod
    def _resolve_app_root() -> Path:
        """Return the install root this service updates.

        ``get_application_root`` ignores its anchor for frozen and Nuitka
        builds and returns the real bundle root, so it stays authoritative
        there. In a source checkout it returns the *anchor's own directory* -
        and this module lives one level down in ``shared/``, so trusting it
        would resolve the root to ``<repo>/shared``, hide the checkout's
        ``.git``, and make every source install report itself as ``packaged``
        (sending it to download an installer instead of fast-forwarding).
        """
        source_root = Path(__file__).resolve().parent.parent
        try:
            from shared.platform_runtime import get_application_root, is_compiled

            if is_compiled():
                return Path(get_application_root(__file__))
        except Exception:
            return source_root
        return source_root

    def current_version(self) -> str:
        # The staging override wins so a release-QA run can simulate an older
        # install even where a caller pins current_version explicitly (Lite and
        # the desktop clients both do).
        return simulated_current_version() or self._current_version or get_version()

    def is_compiled(self) -> bool:
        try:
            from shared.platform_runtime import is_compiled

            return bool(is_compiled())
        except Exception:
            return False

    def install_kind(self) -> str:
        """Return ``source``, ``packaged``, or immutable ``docker``."""
        if _running_in_container():
            return "docker"
        if not self.is_compiled() and (self._app_root / ".git").exists():
            return "source"
        return "packaged"

    def manifest_url(self, channel: Optional[str] = None) -> str:
        template = os.environ.get("AUTOYOU_UPDATE_FEED_URL", "").strip()
        ch = channel or self._channel
        if template:
            return template.replace("{product}", self._product).replace("{channel}", ch)
        return f"{self._feed_base}/{ch}/latest.json"

    def _ensure_enabled(self) -> None:
        if native_store_uri(self._product):
            raise UpdateError("Updates for this installation are managed by the app store.")
        if not self._enabled:
            raise UpdateError("Software updates are disabled; no update request was made")

    @staticmethod
    def _url_origin(url: str) -> tuple[str, str, int]:
        parsed = urlsplit(str(url))
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return parsed.scheme.lower(), str(parsed.hostname or "").lower(), port

    @staticmethod
    def _validate_transport(url: str, label: str) -> None:
        parsed = urlsplit(str(url))
        loopback_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if not parsed.hostname or (parsed.scheme != "https" and not loopback_http):
            raise UpdateError(f"{label} URL must use HTTPS: {url}")

    def _request_headers(self, url: str = "") -> Dict[str, str]:
        # Never forward an OAuth bearer token or session cookie to an artifact
        # host other than the authenticated manifest origin.
        if url and self._url_origin(url) != self._url_origin(self.manifest_url()):
            return {}
        headers: Dict[str, str] = {}
        if self._auth_token:
            value = self._auth_token
            if not value.lower().startswith("bearer "):
                value = f"Bearer {value}"
            headers["Authorization"] = value
        if self._cookie_header:
            headers["Cookie"] = self._cookie_header
        return headers

    @staticmethod
    def _http_status(exc: BaseException) -> int:
        """Best-effort HTTP status from a ``requests`` or ``urllib`` error."""
        candidates = (
            getattr(getattr(exc, "response", None), "status_code", None),
            getattr(exc, "code", None),
            getattr(exc, "status", None),
        )
        for candidate in candidates:
            if candidate is None or isinstance(candidate, bool):
                continue
            try:
                return int(candidate)
            except (TypeError, ValueError):
                continue
        return 0

    @classmethod
    def _transport_error(cls, exc: BaseException, message: str) -> UpdateError:
        """Map a transport failure onto the error type callers can act on."""
        if cls._http_status(exc) in {401, 403}:
            return UpdateAuthError(FEED_AUTH_MESSAGE)
        return UpdateError(message)

    def _urllib_open(self, url: str, timeout: float):
        import urllib.request

        expected_origin = self._url_origin(url)

        class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
            def redirect_request(handler_self, req, fp, code, msg, headers, newurl):
                if self._url_origin(newurl) != expected_origin:
                    raise UpdateError("Update request refused a cross-origin redirect")
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        request = urllib.request.Request(url, headers=self._request_headers(url))
        return urllib.request.build_opener(SameOriginRedirectHandler()).open(request, timeout=timeout)

    # ── manifest fetch + signature verification ───────────────────────────
    def _default_http_get(self, url: str, timeout: float) -> bytes:
        def _read_bounded(blocks) -> bytes:
            body = bytearray()
            for block in blocks:
                if not block:
                    continue
                if len(body) + len(block) > MAX_MANIFEST_BYTES:
                    raise UpdateError("Update manifest exceeds the 1 MiB response limit")
                body.extend(block)
            return bytes(body)

        try:
            import requests  # type: ignore[import]

            with requests.get(url, headers=self._request_headers(url), timeout=timeout, stream=True) as resp:
                resp.raise_for_status()
                return _read_bounded(resp.iter_content(chunk_size=64 * 1024))
        except ImportError:
            with self._urllib_open(url, timeout) as handle:
                return _read_bounded(iter(lambda: handle.read(64 * 1024), b""))

    def verify_envelope(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """Verify a signed manifest envelope and return the parsed payload.

        Fail-closed: raises ``UpdateError`` if no trusted key is configured, the
        key id is unknown, the algorithm is unsupported, or the signature is
        invalid.
        """
        if not isinstance(envelope, dict):
            raise UpdateError("Update manifest envelope is not an object")

        algorithm = str(envelope.get("algorithm") or "").lower()
        if algorithm != "ed25519":
            raise UpdateError(f"Unsupported update signature algorithm: {algorithm or '(none)'}")

        payload = envelope.get("payload")
        if not isinstance(payload, str) or not payload.strip():
            raise UpdateError("Update manifest envelope has no string payload")

        try:
            signature = base64.b64decode(str(envelope.get("signature") or ""), validate=True)
        except Exception as exc:  # noqa: BLE001
            raise UpdateError(f"Update manifest signature is not valid base64: {exc}") from exc
        if not signature:
            raise UpdateError("Update manifest envelope has no signature")

        if not self._trusted_keys:
            raise UpdateError(
                "No trusted update public key configured; refusing to trust the update manifest. "
                "Configure AUTOYOU_UPDATE_PUBLIC_KEY(S) or bundle a release key."
            )

        key_id = str(envelope.get("key_id") or "default")
        public_key = self._trusted_keys.get(key_id)
        if public_key is None:
            raise UpdateError(f"Update manifest signed by unknown key id '{key_id}'")

        try:
            public_key.verify(signature, payload.encode("utf-8"))
        except InvalidSignature as exc:
            raise UpdateError("Update manifest signature verification failed") from exc

        try:
            parsed = json.loads(payload)
        except ValueError as exc:
            raise UpdateError(f"Update manifest payload is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise UpdateError("Update manifest payload is not an object")
        return parsed

    @staticmethod
    def parse_manifest(payload: Dict[str, Any]) -> UpdateManifest:
        if not isinstance(payload, dict):
            raise UpdateError("Update manifest payload is not an object")
        schema_version = payload.get("schema_version")
        if schema_version != MANIFEST_SCHEMA_VERSION:
            raise UpdateError(f"Unsupported update manifest schema: {schema_version!r}")
        product = str(payload.get("product") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", product):
            raise UpdateError("Update manifest is missing a valid 'product'")
        version = str(payload.get("version") or "").strip()
        if not re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,63}", version):
            raise UpdateError("Update manifest is missing a valid route-safe 'version'")
        channel = str(payload.get("channel") or "").strip()
        if channel not in SUPPORTED_CHANNELS:
            raise UpdateError("Update manifest is missing a supported 'channel'")
        artifacts: Dict[str, UpdateArtifact] = {}
        raw_artifacts = payload.get("artifacts") or {}
        if not isinstance(raw_artifacts, dict):
            raise UpdateError("Update manifest 'artifacts' must be an object")
        for key, item in raw_artifacts.items():
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            sha256 = str(item.get("sha256") or "").strip().lower()
            if not url or not sha256:
                continue
            UpdateService._validate_transport(url, "Update artifact")
            if not re.fullmatch(r"[0-9a-f]{64}", sha256):
                raise UpdateError(f"Update artifact '{key}' has an invalid SHA-256")
            size = item.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise UpdateError(f"Update artifact '{key}' is missing a valid byte size")
            artifacts[str(key)] = UpdateArtifact(
                platform_key=str(key),
                url=url,
                sha256=sha256,
                size=size,
            )
        git = payload.get("git") or {}
        if not isinstance(git, dict):
            raise UpdateError("Update manifest 'git' must be an object")
        git_commit = str(git.get("commit") or "").strip()
        if git_commit and not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", git_commit):
            raise UpdateError("Update manifest git commit must be a full hexadecimal object id")
        return UpdateManifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            product=product,
            version=version,
            channel=channel,
            notes=str(payload.get("notes") or ""),
            released_at=str(payload.get("released_at") or ""),
            git_remote=str(git.get("remote") or "origin"),
            git_branch=str(git.get("branch") or "main"),
            git_commit=git_commit,
            artifacts=artifacts,
            raw=payload,
        )

    def fetch_manifest(self, url: Optional[str] = None, *, timeout: float = 15.0) -> UpdateManifest:
        self._ensure_enabled()
        target = url or self.manifest_url()
        self._validate_transport(target, "Update manifest")
        target_host = str(urlsplit(target).hostname or "").lower()
        if target_host not in AUTHORIZED_FEED_HOSTS | {"127.0.0.1", "localhost", "::1"}:
            raise UpdateError(f"Update manifest host is not authorized: {target_host}")
        try:
            body = self._http_get(target, timeout)
        except Exception as exc:  # noqa: BLE001 - network failure surfaced as UpdateError
            raise self._transport_error(
                exc, f"Could not fetch update manifest from {target}: {exc}"
            ) from exc
        if len(body) > MAX_MANIFEST_BYTES:
            raise UpdateError("Update manifest exceeds the 1 MiB response limit")
        try:
            envelope = json.loads(body.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise UpdateError(f"Update manifest at {target} is not valid JSON: {exc}") from exc
        payload = self.verify_envelope(envelope)
        manifest = self.parse_manifest(payload)
        self._validate_manifest_scope(manifest)
        return manifest

    def _validate_manifest_scope(self, manifest: UpdateManifest) -> None:
        if manifest.product != self._product:
            raise UpdateError(
                f"Update manifest product mismatch (expected {self._product!r}, got {manifest.product!r})"
            )
        if manifest.channel != self._channel:
            raise UpdateError(
                f"Update manifest channel mismatch (expected {self._channel!r}, got {manifest.channel!r})"
            )

    # ── version comparison ────────────────────────────────────────────────
    @staticmethod
    def _version_gt(latest: str, current: str) -> bool:
        try:
            from packaging.version import Version

            return Version(latest) > Version(current)
        except Exception:
            # Fallback: tuple compare of dotted integer parts.
            def parts(v: str) -> List[int]:
                out: List[int] = []
                for chunk in str(v).split("."):
                    digits = "".join(ch for ch in chunk if ch.isdigit())
                    out.append(int(digits) if digits else 0)
                return out

            return parts(latest) > parts(current)

    def check_for_update(self, *, timeout: float = 15.0, manifest: Optional[UpdateManifest] = None) -> Dict[str, Any]:
        """Return current/latest versions and whether an update is available."""
        current = self.current_version()
        if native_store_uri(self._product):
            return {
                "current_version": current,
                "latest_version": None,
                "update_available": None,
                "product": self._product,
                "channel": self._channel,
                "install_kind": "packaged",
                "platform_key": platform_key(),
                "store_managed": True,
                "artifact_available": False,
                "artifact": None,
                "message": "Updates are managed by the app store. Open it to check for updates.",
            }
        self._ensure_enabled()
        resolved = manifest or self.fetch_manifest(timeout=timeout)
        self._validate_manifest_scope(resolved)
        plat = platform_key()
        artifact = resolved.artifacts.get(plat)
        return {
            "current_version": current,
            "latest_version": resolved.version,
            "update_available": self._version_gt(resolved.version, current),
            "product": self._product,
            "channel": resolved.channel,
            "install_kind": self.install_kind(),
            "platform_key": plat,
            "notes": resolved.notes,
            "artifact_available": artifact is not None,
            "store_managed": False,
            "artifact": (
                {"url": artifact.url, "sha256": artifact.sha256, "size": artifact.size}
                if artifact
                else None
            ),
            "git": {"remote": resolved.git_remote, "branch": resolved.git_branch, "commit": resolved.git_commit},
        }

    # ── source install: sync to origin/main ───────────────────────────────
    def plan_origin_main_sync(self, manifest: Optional[UpdateManifest] = None) -> List[List[str]]:
        """Return the git command plan to fast-forward the source install.

        Pure (no execution): used by the eval harness/tests and surfaced to the
        operator before applying.
        """
        remote = self._safe_git_ref(manifest.git_remote if manifest else "origin", "remote")
        branch = self._safe_git_ref(manifest.git_branch if manifest else "main", "branch")
        remote_branch = f"{remote}/{branch}"
        plan = [["git", "fetch", remote, branch]]
        if manifest and manifest.git_commit:
            merge_target = self._safe_git_commit(manifest.git_commit)
            plan.append(["git", "merge-base", "--is-ancestor", merge_target, remote_branch])
            plan.append(["git", "merge-base", "--is-ancestor", "HEAD", merge_target])
        else:
            merge_target = remote_branch
        plan.append(["git", "merge", "--ff-only", merge_target])
        return plan

    @staticmethod
    def _safe_git_ref(value: str, label: str) -> str:
        resolved = str(value or "").strip()
        if (
            not resolved
            or resolved.startswith("-")
            or ".." in resolved
            or not re.fullmatch(r"[A-Za-z0-9._/-]+", resolved)
        ):
            raise UpdateError(f"Invalid update git {label}: {resolved!r}")
        return resolved

    @staticmethod
    def _safe_git_commit(value: str) -> str:
        resolved = str(value or "").strip()
        if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", resolved):
            raise UpdateError("Invalid signed update git commit")
        return resolved

    def apply_origin_main_sync(self, manifest: Optional[UpdateManifest]) -> Dict[str, Any]:
        """Fast-forward the source checkout to the signed commit (guarded).

        Refuses when the working tree is dirty or when a fast-forward is not
        possible (diverged), to avoid clobbering local changes. The caller is
        responsible for restarting the process afterwards.
        """
        self._ensure_enabled()
        if manifest is None:
            raise UpdateError("Source updates require a signed manifest with a pinned git commit")
        self._validate_manifest_scope(manifest)
        if not manifest.git_commit:
            raise UpdateError("Signed source update manifest has no pinned git commit")
        if self.install_kind() != "source":
            raise UpdateError("apply_origin_main_sync is only valid for source installs")
        cwd = str(self._app_root)

        def _git(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
            proc = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
            if check and proc.returncode != 0:
                raise UpdateError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
            return proc

        dirty = _git(["status", "--porcelain"], check=False).stdout.strip()
        if dirty:
            raise UpdateError("Working tree has uncommitted changes; refusing to auto-sync origin/main")

        remote = self._safe_git_ref(manifest.git_remote, "remote")
        branch = self._safe_git_ref(manifest.git_branch, "branch")
        _git(["fetch", remote, branch])
        merge_target = f"{remote}/{branch}"
        signed_commit = self._safe_git_commit(manifest.git_commit)
        resolved_commit = _git(["rev-parse", f"{signed_commit}^{{commit}}"], check=False)
        if resolved_commit.returncode != 0:
            raise UpdateError("Signed update commit was not present after fetching the release branch")
        ancestry = _git(["merge-base", "--is-ancestor", resolved_commit.stdout.strip(), merge_target], check=False)
        if ancestry.returncode != 0:
            raise UpdateError("Signed update commit is not part of the fetched release branch")
        merge_target = resolved_commit.stdout.strip()
        before = _git(["rev-parse", "HEAD"], check=False).stdout.strip()
        ancestry = _git(["merge-base", "--is-ancestor", before, merge_target], check=False)
        if ancestry.returncode != 0:
            raise UpdateError("Current checkout is not an ancestor of the signed update commit")
        merge = _git(["merge", "--ff-only", merge_target], check=False)
        if merge.returncode != 0:
            raise UpdateError(
                f"Cannot fast-forward to signed update target (diverged or non-ancestor): "
                f"{merge.stderr.strip() or merge.stdout.strip()}"
            )
        after = _git(["rev-parse", "HEAD"], check=False).stdout.strip()
        if after.lower() != merge_target.lower():
            raise UpdateError("Checkout did not reach the signed update commit")
        return {
            "status": "success",
            "updated": before != after,
            "from_commit": before[:12],
            "to_commit": after[:12],
            "message": "Synced to {0}/{1}. Restart AutoYou to load the new code.".format(remote, branch),
        }

    # ── packaged install: download + verify + stage ───────────────────────
    @staticmethod
    def sha256_file(path: Path, *, chunk: int = 1024 * 1024) -> str:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(chunk), b""):
                digest.update(block)
        return digest.hexdigest()

    def staging_dir(self) -> Path:
        try:
            from shared.platform_runtime import get_user_data_dir

            base = get_user_data_dir("AutoYou")
        except Exception:
            base = self._app_root
        target = Path(base) / "updates"
        target.mkdir(parents=True, exist_ok=True)
        return target

    @staticmethod
    def _safe_artifact_filename(url: str) -> str:
        """Derive a filesystem-safe filename from an artifact URL.

        Uses only the URL path's basename (dropping any ``?query``/``#fragment``)
        and replaces characters that are invalid in Windows filenames.
        """
        from urllib.parse import urlsplit
        import re as _re

        name = os.path.basename(urlsplit(str(url)).path)
        name = _re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._") if name else ""
        return name or "autoyou-update.bin"

    def _stream_to_file(
        self,
        url: str,
        dest: Path,
        timeout: float,
        *,
        expected_size: int,
        chunk: int = 1024 * 1024,
    ) -> None:
        """Stream an HTTP(S) download to disk without buffering it all in memory."""
        def _write_bounded(blocks) -> None:
            written = 0
            with open(dest, "wb") as out:
                for block in blocks:
                    if not block:
                        continue
                    written += len(block)
                    if written > expected_size:
                        raise UpdateError(
                            f"Update artifact exceeded signed size {expected_size}; discarded download"
                        )
                    out.write(block)

        try:
            import requests  # type: ignore[import]

            with requests.get(url, headers=self._request_headers(url), timeout=timeout, stream=True) as resp:
                resp.raise_for_status()
                _write_bounded(resp.iter_content(chunk_size=chunk))
            return
        except ImportError:
            pass
        with self._urllib_open(url, timeout) as response:
            _write_bounded(iter(lambda: response.read(chunk), b""))

    def download_artifact(self, artifact: UpdateArtifact, *, timeout: float = 600.0) -> Path:
        """Download + SHA-256-verify a packaged artifact into the staging dir.

        Real downloads stream to disk (installers can be hundreds of MB); a
        caller-injected ``http_get`` (tests) is used buffered.
        """
        self._ensure_enabled()
        dest = self.staging_dir() / self._safe_artifact_filename(artifact.url)
        try:
            if self._http_get_is_default:
                self._stream_to_file(artifact.url, dest, timeout, expected_size=artifact.size)
            else:
                body = self._http_get(artifact.url, timeout)
                if len(body) > artifact.size:
                    raise UpdateError(f"Update artifact exceeded signed size {artifact.size}")
                dest.write_bytes(body)
        except Exception as exc:  # noqa: BLE001
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            raise self._transport_error(
                exc, f"Could not download update artifact {artifact.url}: {exc}"
            ) from exc
        actual = self.sha256_file(dest)
        if actual.lower() != artifact.sha256.lower():
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            raise UpdateError(
                f"Update artifact SHA-256 mismatch (expected {artifact.sha256}, got {actual}); discarded download"
            )
        actual_size = dest.stat().st_size
        if actual_size != artifact.size:
            dest.unlink(missing_ok=True)
            raise UpdateError(
                f"Update artifact size mismatch (expected {artifact.size}, got {actual_size}); discarded download"
            )
        return dest

    def stage_packaged_update(self, *, timeout: float = 600.0, manifest: Optional[UpdateManifest] = None) -> Dict[str, Any]:
        self._ensure_enabled()
        resolved = manifest or self.fetch_manifest()
        self._validate_manifest_scope(resolved)
        plat = platform_key()
        artifact = resolved.artifacts.get(plat)
        if artifact is None:
            raise UpdateError(f"Update manifest has no artifact for this platform ({plat})")
        path = self.download_artifact(artifact, timeout=timeout)
        return {
            "status": "success",
            "staged_path": str(path),
            "version": resolved.version,
            "platform_key": plat,
            "message": (
                "Verified update staged at {0}. The OS verifies its code signature on install; "
                "launch the installer or restart to apply.".format(path)
            ),
        }

    def docker_update_commands(self, manifest: Optional[UpdateManifest] = None) -> List[str]:
        if manifest is None:
            raise UpdateError("Docker updates require a signed manifest with a pinned git commit")
        self._validate_manifest_scope(manifest)
        if not manifest.git_commit:
            raise UpdateError("Signed Docker update manifest has no pinned git commit")
        signed_commit = self._safe_git_commit(manifest.git_commit)
        plan = self.plan_origin_main_sync(manifest)
        commands = ["if git status --porcelain | grep -q .; then exit 1; fi"]
        commands.extend(shlex.join(command) for command in plan)
        commands.append(f"git merge-base --is-ancestor HEAD {signed_commit}")
        commands.append(f"git merge-base --is-ancestor {signed_commit} HEAD")
        commands.append("docker compose up -d --build autoyou")
        return commands

    def docker_update_command(self, manifest: Optional[UpdateManifest] = None) -> str:
        # Git executes !aliases through its bundled POSIX shell even when the
        # caller is Windows PowerShell 5, so the quoted && chain fails fast on
        # every supported Docker host shell.
        commands = " && ".join(self.docker_update_commands(manifest))
        return f'git -c "alias.autoyou-update=!{commands}" autoyou-update'

    @staticmethod
    def _launch_native_store(uri: str) -> None:
        if os.name == "nt":
            os.startfile(uri)  # type: ignore[attr-defined]
        else:
            subprocess.run(["open", uri], check=True, capture_output=True, timeout=15)

    def _launch_installer(self, path: Path) -> None:
        if self._launcher is not None:
            self._launcher(path)
            return
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], start_new_session=True)
            return
        if path.name.lower().endswith(".appimage"):
            path.chmod(path.stat().st_mode | 0o111)
            subprocess.Popen([str(path)], start_new_session=True)
            return
        subprocess.Popen(["xdg-open", str(path)], start_new_session=True)

    def install_staged_update(self, path: Path) -> Dict[str, Any]:
        """Hand a verified artifact to the platform's native installer."""
        self._ensure_enabled()
        resolved = Path(path)
        if resolved.suffix.lower() == ".whl":
            completed = subprocess.run(
                [sys.executable, "-m", "pip", "install", "--upgrade", str(resolved)],
                text=True,
                capture_output=True,
            )
            if completed.returncode != 0:
                raise UpdateError(completed.stderr.strip() or completed.stdout.strip() or "pip update failed")
            return {
                "status": "success",
                "installed": True,
                "restart_required": True,
                "staged_path": str(resolved),
                "message": "Update installed. Restart AutoYou to load the new version.",
            }
        try:
            self._launch_installer(resolved)
        except OSError as exc:
            raise UpdateError(f"Could not launch the platform installer: {exc}") from exc
        return {
            "status": "success",
            "installed": False,
            "installer_launched": True,
            "restart_required": True,
            "staged_path": str(resolved),
            "message": "Verified update opened in the platform installer. Finish its prompts, then restart AutoYou.",
        }

    def _posix_archive_install_action(self, path: Path) -> Dict[str, Any]:
        return {
            "status": "action_required",
            "installed": False,
            "restart_required": True,
            "staged_path": str(path),
            "command": "tar --extract --gzip --file {0} --directory {1}".format(
                shlex.quote(str(path)),
                shlex.quote(str(self._app_root.parent)),
            ),
            "message": "Quit AutoYou, run the shown extraction command, then launch the updated bundle.",
        }

    def apply_update(
        self,
        *,
        timeout: float = 600.0,
        manifest: Optional[UpdateManifest] = None,
    ) -> Dict[str, Any]:
        """Check, download, and apply or launch the appropriate update path."""
        store_uri = native_store_uri(self._product)
        if store_uri:
            try:
                self._launch_native_store(store_uri)
            except (OSError, subprocess.SubprocessError) as exc:
                raise UpdateError("Could not open the app store. Please open it directly to check for AutoYou updates.") from exc
            return {
                "status": "success",
                "updated": False,
                "store_opened": True,
                **self.check_for_update(),
                "message": "Opened the app store, which manages updates for AutoYou.",
            }
        self._ensure_enabled()
        resolved = manifest or self.fetch_manifest()
        info = self.check_for_update(manifest=resolved)
        if not info["update_available"]:
            return {"status": "success", "updated": False, **info, "message": "AutoYou is up to date."}
        if info["install_kind"] == "source":
            return {**self.apply_origin_main_sync(resolved), **info}
        if info["install_kind"] == "docker":
            command = self.docker_update_command(resolved)
            return {
                "status": "action_required",
                "updated": False,
                "requires_host": True,
                "command": command,
                "commands": self.docker_update_commands(resolved),
                **info,
                "message": "Run the shown fail-fast command from the AutoYou checkout on the Docker host.",
            }
        staged = self.stage_packaged_update(timeout=timeout, manifest=resolved)
        staged_path = Path(staged["staged_path"])
        if info["platform_key"].startswith(("linux-", "wsl-")) and staged_path.name.lower().endswith((".tar.gz", ".tgz")):
            return {**self._posix_archive_install_action(staged_path), **info}
        return {**self.install_staged_update(staged_path), **info}
