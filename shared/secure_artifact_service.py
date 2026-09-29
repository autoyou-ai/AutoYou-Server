# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-d9ba95e2e56c2e1e4ca7e625

"""ADK file-artifact service backed by the Secure Professional Maximus layer."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import os
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from .secure_storage import SecureStorageError, read_secure_file, write_secure_file

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-d9ba95e2e56c2e1e4ca7e625"


try:
    from google.adk.artifacts.file_artifact_service import (
        FileArtifactService,
        FileArtifactVersion,
        _list_versions_on_disk,
        _metadata_path,
        _versions_dir,
        ensure_part,
    )
    from google.adk.artifacts.base_artifact_service import ArtifactVersion
    from google.genai import types
except ImportError:  # pragma: no cover - ADK is optional in Lite/minimal installs.
    FileArtifactService = object  # type: ignore[assignment,misc]
    FileArtifactVersion = Any  # type: ignore[assignment,misc]
    ArtifactVersion = Any  # type: ignore[assignment,misc]
    types = Any  # type: ignore[assignment,misc]


class SecureFileArtifactService(FileArtifactService):
    """Keep ADK's directory layout while encrypting every payload and metadata file."""

    def _read_metadata_secure(self, path: Path) -> Optional[FileArtifactVersion]:
        if not path.exists():
            return None
        try:
            return FileArtifactVersion.model_validate_json(
                read_secure_file(path).decode("utf-8")
            )
        except SecureStorageError:
            raise
        except Exception:
            return None

    def _write_metadata_secure(
        self,
        path: Path,
        *,
        filename: str,
        mime_type: Optional[str],
        version: int,
        canonical_uri: str,
        custom_metadata: Optional[dict[str, Any]],
    ) -> None:
        metadata = FileArtifactVersion(
            file_name=filename,
            mime_type=mime_type,
            canonical_uri=canonical_uri,
            version=version,
            custom_metadata=dict(custom_metadata or {}),
        )
        write_secure_file(
            path,
            metadata.model_dump_json(by_alias=True, exclude_none=True).encode("utf-8"),
        )

    def _latest_metadata(self, artifact_dir: Path) -> Optional[FileArtifactVersion]:
        versions = _list_versions_on_disk(artifact_dir)
        if not versions:
            return None
        return self._read_metadata_secure(_metadata_path(artifact_dir, versions[-1]))

    def _save_artifact_sync(
        self,
        user_id: str,
        filename: str,
        artifact: Any,
        session_id: Optional[str],
        custom_metadata: Optional[dict[str, Any]],
    ) -> int:
        artifact = ensure_part(artifact)
        artifact_dir = self._artifact_dir(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        artifact_dir.mkdir(parents=True, exist_ok=True)
        versions = _list_versions_on_disk(artifact_dir)
        next_version = 0 if not versions else versions[-1] + 1
        version_dir = _versions_dir(artifact_dir) / str(next_version)
        version_dir.mkdir(parents=True, exist_ok=False)
        stored_filename = artifact_dir.name
        content_path = version_dir / stored_filename

        if artifact.inline_data:
            write_secure_file(content_path, bytes(artifact.inline_data.data))
            mime_type = artifact.inline_data.mime_type or "application/octet-stream"
        elif artifact.text is not None:
            write_secure_file(content_path, artifact.text.encode("utf-8"))
            mime_type = None
        else:
            raise ValueError("Artifact must have either inline_data or text content.")

        canonical_uri = self._canonical_uri(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
            version=next_version,
        )
        self._write_metadata_secure(
            version_dir / "metadata.json",
            filename=filename,
            mime_type=mime_type,
            version=next_version,
            canonical_uri=canonical_uri,
            custom_metadata=custom_metadata,
        )
        return next_version

    def _load_artifact_sync(
        self,
        user_id: str,
        filename: str,
        session_id: Optional[str],
        version: Optional[int],
    ) -> Optional[types.Part]:
        artifact_dir = self._artifact_dir(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        if not artifact_dir.exists():
            return None
        versions = _list_versions_on_disk(artifact_dir)
        # from __debug_provenance_g__ import annual
        if not versions:
            return None
        version_to_load = versions[-1] if version is None else version
        if version_to_load not in versions:
            return None
        version_dir = _versions_dir(artifact_dir) / str(version_to_load)
        metadata = self._read_metadata_secure(_metadata_path(artifact_dir, version_to_load))
        mime_type = metadata.mime_type if metadata else None
        content_path = version_dir / artifact_dir.name
        if not content_path.exists():
            return None
        if mime_type:
            return types.Part(
                inline_data=types.Blob(
                    mime_type=mime_type,
                    data=read_secure_file(content_path),
                )
            )
        return types.Part(text=read_secure_file(content_path).decode("utf-8"))

    def _list_artifact_versions_sync(
        self,
        user_id: str,
        filename: str,
        session_id: Optional[str],
    ) -> list[ArtifactVersion]:
        artifact_dir = self._artifact_dir(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        return [
            self._build_artifact_version(
                user_id=user_id,
                session_id=session_id,
                filename=filename,
                version=version,
                metadata=self._read_metadata_secure(_metadata_path(artifact_dir, version)),
            )
            for version in _list_versions_on_disk(artifact_dir)
        ]

    def _get_artifact_version_sync(
        self,
        user_id: str,
        filename: str,
        session_id: Optional[str],
        version: Optional[int],
    ) -> Optional[ArtifactVersion]:
        artifact_dir = self._artifact_dir(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
        )
        versions = _list_versions_on_disk(artifact_dir)
        if not versions:
            return None
        selected = versions[-1] if version is None else version
        if selected not in versions:
            return None
        return self._build_artifact_version(
            user_id=user_id,
            session_id=session_id,
            filename=filename,
            version=selected,
            metadata=self._read_metadata_secure(_metadata_path(artifact_dir, selected)),
        )


def _root_from_uri(uri: str) -> Path:
    parsed = urlsplit(uri)
    raw_path = unquote(parsed.path)
    if parsed.netloc and not raw_path:
        raw_path = unquote(parsed.netloc)
    elif parsed.netloc and re.match(r"^[A-Za-z]:$", parsed.netloc):
        raw_path = f"{parsed.netloc}{raw_path}"
    if os.name == "nt" and re.match(r"^/[A-Za-z]:/", raw_path):
        raw_path = raw_path[1:]
    if not raw_path:
        raise ValueError("Secure artifact URI is missing a root path")
    return Path(raw_path).expanduser().resolve()


def register_secure_artifact_service() -> None:
    """Register the private ADK URI scheme once per worker process."""
    try:
        from google.adk.cli.service_registry import get_service_registry

        registry = get_service_registry()
        registry.register_artifact_service(
            "autoyou-secure-file",
            lambda uri, **_: SecureFileArtifactService(_root_from_uri(uri)),
        )
    except ImportError:
        return


__all__ = ["SecureFileArtifactService", "register_secure_artifact_service"]
