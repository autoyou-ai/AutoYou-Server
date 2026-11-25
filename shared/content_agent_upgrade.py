"""Read content saved by the released 81.7 agents under their 81.8 identities."""
from pathlib import Path
import os
import re

from filelock import FileLock

PREVIOUS_NAMES = {
    "music_agent": "audio_agent", "media_agent": "tv_agent", "games_agent": "game_agent",
    "reminders_agent": "notify_agent", "profile_agent": "persona_agent", "diary_agent": "persona_agent",
}


def saved_value(values, name, fallback=None):
    return values.get(name, values.get(PREVIOUS_NAMES.get(name), fallback))


def storage_path(root: Path, name: str, *, suffix: str = "") -> Path:
    """Keep absolute paths in existing libraries valid. New libraries use the canonical name."""
    current = root / (name + suffix)
    previous = PREVIOUS_NAMES.get(name)
    if previous and not current.exists():
        old = root / (previous + suffix)
        if old.is_dir():
            return old
    return current


def upgrade_private_documents(root: Path) -> None:
    """Split the old mixed document once, retaining the original until the owner removes it."""
    from shared.secure_data_store import ENC_HEADER, SecureDataError, SecureDataStore
    from shared.secure_storage import read_secure_file

    old = root / "persona_agent"
    legacy = old / "persona.md"
    marker = root / ".private-content-81-8"
    if marker.exists() or not legacy.is_file():
        return
    with FileLock(str(marker) + ".lock", timeout=15):
        if marker.exists():
            return
        def read_old(path):
            store = SecureDataStore(path, keystore_service="AutoYou-PersonaData", keystore_username="persona")
            return store.read(migrate_plaintext=False)
        def protected(path):
            return read_secure_file(path, migrate_plaintext=False).startswith(ENC_HEADER.encode("ascii"))
        text = read_old(legacy)
        if text is None:
            return
        block = re.compile(r"(?ms)^<!-- autoyou-diary:([0-9a-fA-F-]{36}) -->\n### \d{4}-\d{2}-\d{2}\n.*?^<!-- /autoyou-diary:\1 -->$")
        entries = [m.group() for m in block.finditer(text)]
        profile = block.sub("", text).strip()
        # An incomplete marker could contain journal text. Keep the protected original for recovery.
        if "<!-- autoyou-diary:" in profile or "<!-- /autoyou-diary:" in profile:
            raise SecureDataError("The old journal contains an incomplete entry. Its original file was preserved.")
        def new_file(agent, relative, value, independent):
            dest = root / (agent + "_agent")
            dest.mkdir(parents=True, exist_ok=True)
            if independent:
                (dest / ".independent-key").touch(exist_ok=True)
            target = SecureDataStore(dest / relative, encrypt=independent, keystore_service="AutoYou-" + agent.title() + "Data", keystore_username=agent)
            if target.exists() and target.read() != value:
                raise SecureDataError("The new document already has different content. Both files were preserved.")
            if not target.exists():
                target.write(value)
            if target.read() != value:
                raise SecureDataError("The upgraded private document could not be verified.")
        independent = protected(legacy) or os.environ.get("AUTOYOU_PERSONA_ENCRYPT", "").lower() in {"1","true","yes","on"}
        if profile:
            new_file("profile", "profile.md", profile, independent)
        if entries:
            journal = "# Diary\n\n" + "\n\n".join(entries) + "\n"
            journal = journal.replace("/agent/persona_agent/api/persona/entries/", "/agent/diary_agent/api/diary/entries/")
            new_file("diary", "diary.md", journal, independent)
        for media in sorted((old / "media").glob("*.json")):
            if media.is_file() and not media.is_symlink():
                new_file("diary", Path("media") / media.name, read_old(media), independent or protected(media))
        marker.touch()
