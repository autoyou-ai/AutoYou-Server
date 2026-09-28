# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-425a59663352447455546d73-c2c19e75b74ef9960f2090d3

"""First-run detection and one-time LICENSE / agreement acknowledgement.

This mirrors the JAILBREAK acknowledgement design (a marker file in the writable
mutable data dir) so it behaves IDENTICALLY across every runtime AutoYou ships in:

- source runs (``server.py`` / ``python -m autoyou_lite.server``) via
  ``scripts/bootstrap_autoyou.py``
- compiled Windows ``.exe``, macOS ``.app``, and Linux standalone binaries
- WSL / Ubuntu / bare Linux
- Docker containers (mount the data dir as a volume to persist the acknowledgement
  across container recreation)

Why a marker file in the mutable data dir? Because ``get_mutable_data_dir`` already
resolves to the correct writable location in each environment (the per-user data
dir for compiled builds, the app/workspace root for source runs, a pytest-scoped
root under tests) and never points inside a read-only application bundle. The
JAILBREAK acknowledgement uses the exact same location and is proven across all
these targets, so the first-run acknowledgement inherits that portability for free.

This module is detection + record/clear only. Whether the admin UI *blocks* first
unlock until the agreement is accepted is wired separately by the server/lite
login flow - keeping the cross-environment primitive here and the product policy
at the call site.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-425a59663352447455546d73-c2c19e75b74ef9960f2090d3"


import datetime as _datetime
import time
from pathlib import Path
from typing import Any, Dict, Optional

from shared.platform_runtime import get_mutable_data_dir
from shared.secure_storage import (
    FILE_HEADER as SECURE_FILE_HEADER,
    SQLITE_HEADER as SECURE_SQLITE_HEADER,
    SecureStorageError,
    load_secure_json,
    save_secure_json,
    secure_storage_enabled,
)

#: Marker file recording that the operator accepted the LICENSE / agreement.
LICENSE_ACK_FILENAME = "LICENSE_ACKNOWLEDGEMENT"

#: Bump when the agreement text changes materially and re-acceptance is required.
CURRENT_AGREEMENT_VERSION = "2026-07-09"


def _ack_dir(app_name: str = "AutoYou", anchor: Optional[str | Path] = None) -> Path:
    data_dir = get_mutable_data_dir(app_name, anchor=anchor)
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def license_ack_path(app_name: str = "AutoYou", anchor: Optional[str | Path] = None) -> Path:
    """Absolute path to the LICENSE acknowledgement marker for this runtime."""
    return _ack_dir(app_name, anchor=anchor) / LICENSE_ACK_FILENAME


def is_license_acknowledgement_pending_unlock(
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> bool:
    """Whether an existing acknowledgement is sealed until Maximus unlocks.

    This is intentionally a header-only check.  A recovery page must not
    mistake an unreadable, previously accepted agreement for a new agreement,
    and it must not attempt to decrypt protected data before its original
    credential boundary is available.
    """
    if secure_storage_enabled():
        return False
    path = license_ack_path(app_name, anchor=anchor)
    try:
        with path.open("rb") as handle:
            return handle.read(max(len(SECURE_FILE_HEADER), len(SECURE_SQLITE_HEADER))).startswith(
                (SECURE_FILE_HEADER, SECURE_SQLITE_HEADER)
            )
    except OSError:
        return False


def read_license_acknowledgement(
    app_name: str = "AutoYou", anchor: Optional[str | Path] = None
) -> Optional[Dict[str, Any]]:
    """Return the parsed acknowledgement record, or None when not present."""
    path = license_ack_path(app_name, anchor=anchor)
    if not path.exists():
        return None
    try:
        return load_secure_json(path, default=None)
    except SecureStorageError:
        # Preserve the historical plain-text marker fallback when the secure
        # boundary is not active. A protected marker cannot prove acceptance
        # until its original Maximus boundary is restored, so treat it as
        # unacknowledged instead of breaking the public login page.
        if secure_storage_enabled():
            raise
        try:
            protected = path.read_bytes().startswith(
                (SECURE_FILE_HEADER, SECURE_SQLITE_HEADER)
            )
        except OSError:
            raise
        if protected:
            return None
        return {"agreement_version": "", "legacy": True}
    except Exception:
        # A present-but-unparseable marker is treated as a legacy/plain accept.
        return {"agreement_version": "", "legacy": True}


def is_license_acknowledged(
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
    *,
    required_version: Optional[str] = CURRENT_AGREEMENT_VERSION,
) -> bool:
    """True when the LICENSE has been acknowledged for ``required_version``.

    Pass ``required_version=None`` to accept any prior acknowledgement regardless
    of the agreement version it was recorded against.
    """
    record = read_license_acknowledgement(app_name, anchor=anchor)
    if record is None:
        return False
    if required_version is None:
        return True
    if record.get("legacy"):
        # Unparseable legacy marker predates versioning; require re-acceptance.
        return False
    return str(record.get("agreement_version", "")) == str(required_version)


def record_license_acknowledgement(
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
    *,
    agreement_version: str = CURRENT_AGREEMENT_VERSION,
    accepted_by: str = "admin",
) -> Path:
    """Persist that the operator accepted the LICENSE / agreement."""
    path = license_ack_path(app_name, anchor=anchor)
    payload = {
        "agreement_version": str(agreement_version),
        "accepted_by": str(accepted_by),
        "accepted_at": time.time(),
        "accepted_at_iso": _datetime.datetime.now().isoformat(),
    }
    save_secure_json(path, payload)
    return path


def clear_license_acknowledgement(
    app_name: str = "AutoYou", anchor: Optional[str | Path] = None
) -> bool:
    """Remove the acknowledgement marker (used by 'wipe' / reset flows)."""
    path = license_ack_path(app_name, anchor=anchor)
    if path.exists():
        path.unlink()
        return True
    return False


def is_first_run(
    config_exists: bool,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> bool:
    """Return True when this looks like a clean first run.

    ``config_exists`` is supplied by the caller (e.g. server ``_saved_config_exists()``
    or autoyou-lite's persisted-config check) because the config location is
    runtime-specific; the acknowledgement marker is the cross-environment half.
    A run is "first" when neither a saved config nor an acknowledgement exists.
    """
    if config_exists:
        return False
    return read_license_acknowledgement(app_name, anchor=anchor) is None
