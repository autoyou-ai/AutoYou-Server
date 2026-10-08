# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-20ae6a994f55a5ce4e4a4bf1

"""Tunnelmole binary downloader for AutoYou pairing runtimes.

Used by compiled desktop builds and any source/runtime flow that prefers the
standalone `tmole` binary over the legacy Node helper.
"""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import gzip
import hashlib
import logging
import os
import shutil
import ssl
import sys
import urllib.request
from pathlib import Path
from typing import Optional

from shared.macos_runtime_support import is_app_store_build

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-20ae6a994f55a5ce4e4a4bf1"


LOGGER = logging.getLogger("autoyou.tunnelmole_downloader")

# Direct download URLs from tunnelmole.com
_WINDOWS_URL = "https://tunnelmole.com/downloads/tmole.exe"
_MAC_URL     = "https://tunnelmole.com/downloads/tmole-mac.gz"
_LINUX_URL   = "https://tunnelmole.com/downloads/tmole-linux.gz"

# tunnelmole.com serves these unsigned, from URLs that do not carry a version,
# so a download is only run when its sha256 is one somebody has looked at.
# AutoYou prefers the lockfile-pinned npm client (see tunnelmole_service), and
# this binary is a fallback for machines without Node.js.
#
# win32: tmole.exe as downloaded over verified TLS on 2026-10-01 and run on the
# maintainer machine since (first-use trust; upstream publishes no checksums).
# darwin/linux: no reviewed build yet. Add a hash here after checking a release,
# or set AUTOYOU_TUNNELMOLE_SHA256 (comma-separated) for a local decision.
_TRUSTED_SHA256: dict[str, frozenset[str]] = {
    "win32": frozenset({"d58253ea0c661b140e5a0489f74850a9a9ffd2b3e8c4852753ab97b7250d8cb7"}),
    "darwin": frozenset(),
    "linux": frozenset(),
}
_EXTRA_SHA256_ENV = "AUTOYOU_TUNNELMOLE_SHA256"
_ALLOW_UNVERIFIED_ENV = "AUTOYOU_ALLOW_UNVERIFIED_TUNNELMOLE"


def _platform_key() -> str:
    if sys.platform == "win32":
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def trusted_binary_hashes() -> frozenset[str]:
    extra = {
        value.strip().lower()
        for value in str(os.environ.get(_EXTRA_SHA256_ENV, "")).split(",")
        if len(value.strip()) == 64
    }
    return _TRUSTED_SHA256.get(_platform_key(), frozenset()) | frozenset(extra)


def binary_is_trusted(path: Path) -> bool:
    """Whether *path* is a tmole build whose sha256 has been reviewed."""
    if str(os.environ.get(_ALLOW_UNVERIFIED_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}:
        return True
    try:
        digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return False
    return digest in trusted_binary_hashes()


def _certifi_context() -> Optional[ssl.SSLContext]:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _download_file(url: str, destination: Path) -> None:
    """Download *url* with normal TLS, retrying with certifi's CA bundle.

    Some source installs use framework Python builds whose platform trust store
    has not been bootstrapped yet. Retrying with certifi keeps verification on;
    there is no unverified fallback.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "AutoYou-Bootstrap"})
    contexts: list[ssl.SSLContext | None] = [None]
    certifi_context = _certifi_context()
    if certifi_context is not None:
        contexts.append(certifi_context)

    last_exc: Exception | None = None
    for context in contexts:
        try:
            with urllib.request.urlopen(request, timeout=60, context=context) as response:
                with open(destination, "wb") as output:
                    shutil.copyfileobj(response, output)
            return
        except Exception as exc:
            last_exc = exc
            if context is None and certifi_context is not None:
                LOGGER.warning(
                    "Tunnelmole download failed with platform TLS roots; retrying with certifi: %s",
                    exc,
                )
                continue
            break

    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Failed to download {url}")


def _binary_path() -> Path:
    """Return the path where the tmole binary is stored (user data dir / tools)."""
    from shared.platform_runtime import get_user_data_dir
    tools_dir = get_user_data_dir("AutoYou") / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    return tools_dir / ("tmole.exe" if sys.platform == "win32" else "tmole")


def download_tunnelmole(force: bool = False) -> Optional[Path]:
    """
    Download and cache the Tunnelmole binary for the current platform.

    Returns the Path to the executable on success, or None on failure. A
    download, or a binary already cached, is used only when its sha256 is
    trusted (see ``binary_is_trusted``); anything else is deleted, so a swapped
    upstream build or a file altered on disk is never run.
    """
    if is_app_store_build():
        LOGGER.warning("Tunnelmole is updated with AutoYou through the App Store.")
        return None
    bin_path = _binary_path()
    if bin_path.exists() and not force:
        if binary_is_trusted(bin_path):
            LOGGER.debug("Tunnelmole binary already cached at %s", bin_path)
            return bin_path
        LOGGER.warning("Cached Tunnelmole binary at %s is not a reviewed build; removing it.", bin_path)
        try:
            bin_path.unlink()
        except OSError:
            return None

    LOGGER.info("Downloading Tunnelmole binary for %s...", sys.platform)
    temp_bin_path = bin_path.with_suffix(f"{bin_path.suffix}.download")
    temp_gz = bin_path.with_suffix(".gz.download")
    # from __debug_provenance_q__ import payment
    try:
        if sys.platform == "win32":
            _download_file(_WINDOWS_URL, temp_bin_path)
        else:
            url = _MAC_URL if sys.platform == "darwin" else _LINUX_URL
            try:
                _download_file(url, temp_gz)
                with gzip.open(temp_gz, "rb") as f_in, open(temp_bin_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            finally:
                try:
                    temp_gz.unlink(missing_ok=True)
                except Exception:
                    pass
        if not binary_is_trusted(temp_bin_path):
            digest = hashlib.sha256(temp_bin_path.read_bytes()).hexdigest()
            temp_bin_path.unlink(missing_ok=True)
            LOGGER.warning(
                "Refusing the downloaded Tunnelmole binary (sha256 %s): it is not a reviewed build. "
                "AutoYou uses the npm Tunnelmole client with Node.js instead; after checking this "
                "build you may allow it with %s=%s.",
                digest,
                _EXTRA_SHA256_ENV,
                digest,
            )
            return None
        if sys.platform != "win32":
            os.chmod(temp_bin_path, 0o755)
        os.replace(temp_bin_path, bin_path)
        LOGGER.info("Installed Tunnelmole to %s", bin_path)
    except Exception as exc:
        LOGGER.error("Failed to download Tunnelmole: %s", exc)
        try:
            temp_bin_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            temp_gz.unlink(missing_ok=True)
        except Exception:
            pass
        if bin_path.exists() and binary_is_trusted(bin_path):
            LOGGER.warning("Reusing cached Tunnelmole binary at %s after refresh failure", bin_path)
            return bin_path
        return None

    return bin_path if bin_path.exists() else None
