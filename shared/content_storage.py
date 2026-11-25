"""Verified content-addressed storage. Never removes an external original.

Catalog paths retain agent provenance. managed_path points at the immutable
object used by My Content. Supported owned media keep their original filename
as a small reference, so Audio/TV links survive duplicate consolidation.
"""
from pathlib import Path
import time
import uuid

from shared import content_files
from shared.content_library import ContentLibrary, managed_roots, settings
from shared.secure_storage import secure_storage_enabled

REFERENCE_SOURCES = {"music_agent", "media_agent", "media_generation_agent", "files_agent"}


def stamp(path):
    path = Path(path)
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def owned(path):
    path = Path(path)
    return not path.is_symlink() and any(path.resolve().is_relative_to(root) for root in managed_roots())


def resolve_path(row) -> Path:
    """Resolve the readable path for a catalog row, recovering across migrated or restored managed roots."""
    import re
    from shared.content_library import output_roots
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    managed = str(row["managed_path"] or "") if "managed_path" in keys else ""
    primary = str(row["path"] or "") if "path" in keys else ""
    digest = str(row["digest"] or "") if "digest" in keys else ""
    for candidate_str in (managed, primary):
        if candidate_str:
            candidate = Path(candidate_str)
            if candidate.is_file():
                if candidate_str == managed and not (
                    re.fullmatch(r"[a-f0-9]{64}", digest) and not candidate.is_symlink()
                    and any(candidate.resolve() == (root / ".objects" / digest[:2] / digest).resolve()
                            and candidate.resolve().is_relative_to(root.resolve()) for root in managed_roots())
                ):
                    if primary == managed:
                        primary = ""
                    managed = ""
                    continue
                return candidate
    if re.fullmatch(r"[a-f0-9]{64}", digest):
        for root in managed_roots():
            obj = root / ".objects" / digest[:2] / digest
            if obj.is_file() and not obj.is_symlink() and obj.resolve().is_relative_to(root.resolve()):
                return obj
    source = str(row["source"] or "") if "source" in keys else ""
    if primary and source:
        name = Path(primary).name
        for root in output_roots(source):
            relocated = root / name
            if relocated.is_file() and not relocated.is_symlink() and relocated.resolve().is_relative_to(root.resolve()):
                # A common basename is not enough to identify restored media.
                if digest and content_files.fingerprint(relocated)[0] == digest:
                    return relocated
    return Path(managed or primary)


def store_object(path, root=None, cancelled=lambda: False):
    """Return a SHA-256 verified object, with no whole-media allocation."""
    path = Path(path)
    before = stamp(path)
    root = Path(root or settings()["output_root"]).resolve()
    if root not in managed_roots():
        raise ValueError("Destination must be a configured managed folder")
    staged = root / ".objects" / ("pending-" + uuid.uuid4().hex)
    content_files.create(staged)
    try:
        import hashlib
        digest, size = hashlib.sha256(), 0
        for chunk in content_files.read_range(path):
            if cancelled():
                raise InterruptedError("Library operation cancelled")
            digest.update(chunk)
            size = content_files.append(staged, size, chunk)
        if stamp(path) != before:
            raise OSError("Source changed during verification; retry when it is finished")
        checksum = digest.hexdigest()
        # Verify the actual stored/decrypted bytes before changing a pointer.
        if content_files.fingerprint(staged) != (checksum, size):
            raise OSError("Stored content did not pass verification")
        target = root / ".objects" / checksum[:2] / checksum
        if target.exists():
            if content_files.fingerprint(target) != (checksum, size):
                raise OSError("Existing content object failed verification")
            if secure_storage_enabled() and not content_files.protected(target):
                content_files.seal(target)
        else:
            content_files.promote(staged, target)
        return {"managed_path": str(target), "digest": checksum, "byte_size": size}
    finally:
        if staged.exists():
            content_files.discard(staged)


def index_file(row, *, copy_external=False, destination=None, cancelled=lambda: False):
    path = Path(row["path"])
    if not row["path"] or not path.is_file():
        return {"organization_state": "missing"} if row["path"] else {}
    current_stamp = stamp(path)
    if not copy_external and row["file_stamp"] == current_stamp and row["digest"]:
        return {}
    # Only known completed media contracts are rewritten to references.
    can_reference = owned(path) and (row["source"] in REFERENCE_SOURCES or row["source"].startswith("custom_"))
    if can_reference or copy_external:
        result = store_object(path, destination, cancelled)
        if can_reference:
            content_files.make_reference(path, result["managed_path"], result["digest"])
    else:
        digest, size = content_files.fingerprint(path)
        if stamp(path) != current_stamp:
            raise OSError("Source changed during verification")
        result = {"digest": digest, "byte_size": size}
        # A stale managed copy must not hide changes made in a source agent.
        if row["file_stamp"] != current_stamp:
            result["managed_path"] = ""
    result["file_stamp"] = stamp(path)
    return result


def duplicates(library=None, *, allowed_sources=None):
    library = library or ContentLibrary()
    source_filter = ""
    source_args: list[str] = []
    if allowed_sources is not None:
        ordered = sorted(set(allowed_sources))
        if not ordered:
            return []
        source_filter = " AND source IN (" + ",".join("?" for _ in ordered) + ")"
        source_args = ordered
    with library.connect() as db:
        groups = db.execute(
            f"""SELECT digest,MAX(byte_size) AS bytes,COUNT(*) AS count FROM content
            WHERE deleted=0 AND digest!=''{source_filter} GROUP BY digest HAVING COUNT(*)>1 ORDER BY bytes DESC LIMIT 100""",
            source_args,
        ).fetchall()
        return [
            {
                "digest": group["digest"],
                "byte_size": group["bytes"],
                "items": [
                    library.public(row)
                    for row in db.execute(
                        f"SELECT * FROM content WHERE deleted=0 AND digest=?{source_filter}",
                        (group["digest"], *source_args),
                    )
                ],
            }
            for group in groups
        ]


def migration_plan(destination):
    """Read-only review. Executing a plan copies and verifies; it never deletes originals."""
    root = Path(destination).expanduser()
    if not root.is_absolute() or root == Path(root.anchor):
        raise ValueError("Choose an absolute folder inside a drive")
    import os
    test = os.getenv("AUTOYOU_TEST_ROOT")
    if test and not root.resolve().is_relative_to(Path(test).resolve()):
        raise ValueError("Test content must remain inside AUTOYOU_TEST_ROOT")
    library = ContentLibrary()
    with library.connect() as db:
        rows = db.execute("SELECT id,byte_size,path,managed_path FROM content WHERE deleted=0 AND path!=''").fetchall()
    # Hashing/copying is queued, never performed by this preview request.
    return {"destination": str(root.resolve()), "items": len(rows),
            "known_bytes": sum(row["byte_size"] for row in rows),
            "unmeasured": sum(not row["byte_size"] for row in rows),
            "preserves_originals": True, "message": "Copy, verify SHA-256, then update library links. Originals and agent links stay available."}
