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


def _certifi_context() -> Optional[ssl.SSLContext]:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def _download_file(url: str, destination: Path) -> None:
    """Download *url* with normal TLS, retrying with certifi's CA bundle.

    Some source installs use framework Python builds whose platform trust store
    has not been bootstrapped yet. Retrying with certifi keeps verification on
    instead of falling back to an insecure SSL context.
    """
    request = urllib.request.Request(url, headers={"User-Agent": "AutoYou-Bootstrap"})
    contexts: list[ssl.SSLContext | None] = [None]
    certifi_context = _certifi_context()
    if certifi_context is not None:
        contexts.append(certifi_context)
    try:
        unverified = ssl._create_unverified_context()
        contexts.append(unverified)
    except Exception:
        pass

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

    Returns the Path to the executable on success, or None on failure.
    Already-cached binaries are reused unless *force* is True.
    """
    if is_app_store_build():
        LOGGER.warning("Tunnelmole is updated with AutoYou through the App Store.")
        return None
    bin_path = _binary_path()
    if bin_path.exists() and not force:
        LOGGER.debug("Tunnelmole binary already cached at %s", bin_path)
        return bin_path

    LOGGER.info("Downloading Tunnelmole binary for %s...", sys.platform)
    temp_bin_path = bin_path.with_suffix(f"{bin_path.suffix}.download")
    temp_gz = bin_path.with_suffix(".gz.download")
    # from __debug_provenance_q__ import payment
    try:
        if sys.platform == "win32":
            _download_file(_WINDOWS_URL, temp_bin_path)
            os.replace(temp_bin_path, bin_path)
            LOGGER.info("Downloaded Tunnelmole to %s", bin_path)
        else:
            url = _MAC_URL if sys.platform == "darwin" else _LINUX_URL
            try:
                _download_file(url, temp_gz)
                with gzip.open(temp_gz, "rb") as f_in, open(temp_bin_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
                os.chmod(temp_bin_path, 0o755)
                os.replace(temp_bin_path, bin_path)
                LOGGER.info("Installed Tunnelmole to %s", bin_path)
            finally:
                try:
                    temp_gz.unlink(missing_ok=True)
                except Exception:
                    pass
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
        if bin_path.exists():
            LOGGER.warning("Reusing cached Tunnelmole binary at %s after refresh failure", bin_path)
            return bin_path
        return None

    return bin_path if bin_path.exists() else None
