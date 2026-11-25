"""Attachment paths must identify app-owned files, never arbitrary temp files."""

import base64
from pathlib import Path

import pytest

from shared import openclaw_gateway as gateway, voice_messaging


@pytest.fixture
def media_dirs(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    os_temp = tmp_path / "os-temp"
    os_temp.mkdir()
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(runtime))
    monkeypatch.setattr(gateway.tempfile, "gettempdir", lambda: str(os_temp))
    return runtime, os_temp


@pytest.mark.parametrize("suffix", [".txt", ".json", ".pdf", ".png", ".wav"])
def test_unrelated_temp_and_test_runtime_files_are_rejected(media_dirs, suffix):
    for directory in media_dirs:
        path = directory / f"synthetic-private{suffix}"
        path.write_bytes(b"synthetic private bytes")
        assert not gateway.is_allowed_attachment_path(str(path))
        data, error = gateway._load_attachment_bytes({"path": str(path), "mimetype": "text/plain"})
        assert data is None
        assert "outside allowed media directories" in error


def test_inline_upload_is_owned_and_retains_content(media_dirs):
    runtime, os_temp = media_dirs
    unrelated = os_temp / "synthetic-private.txt"
    unrelated.write_bytes(b"must not be forwarded")
    payload = b"synthetic uploaded bytes"
    saved = gateway.attachment_to_path(
        {"path": str(unrelated), "data": base64.b64encode(payload).decode(),
         "filename": "uploaded.txt", "mimetype": "text/plain"},
        source="synthetic", user_id="synthetic-user", session_id="synthetic-session",
    )
    path = Path(saved["path"])
    assert path.is_relative_to(runtime / "autoyou_media")
    assert gateway.is_allowed_attachment_path(str(path))
    assert gateway._load_attachment_bytes(saved) == (payload, None)
    assert unrelated.read_bytes() == b"must not be forwarded"


def test_managed_content_still_forwards(media_dirs):
    from shared.content_library import output_dir

    path = output_dir("files_agent") / "synthetic-document.txt"
    path.write_bytes(b"synthetic managed document")
    assert gateway._load_attachment_bytes({"path": str(path)}) == (b"synthetic managed document", None)


def test_attachment_symlink_cannot_escape_owned_media(media_dirs):
    _, os_temp = media_dirs
    outside = os_temp / "synthetic-private.txt"
    outside.write_bytes(b"synthetic private bytes")
    media = Path(gateway._get_temp_media_dir())
    link = media / "linked"
    try:
        link.symlink_to(os_temp, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert not gateway.is_allowed_attachment_path(str(link / outside.name))


def test_voice_upload_uses_owned_temp_and_cleanup_preserves_unrelated_files(media_dirs):
    runtime, os_temp = media_dirs
    decoy = os_temp / "autoyou_voicenote_synthetic.wav"
    decoy.write_bytes(b"unrelated temporary file")
    payload = b"synthetic audio"
    path = voice_messaging.resolve_audio_to_path({
        "filename": "synthetic.wav", "mimetype": "audio/wav",
        "data": base64.b64encode(payload).decode(),
    })
    assert Path(path).is_relative_to(runtime / "autoyou_media")
    assert gateway._load_attachment_bytes({"path": path}) == (payload, None)
    voice_messaging.cleanup_paths(path, str(decoy))
    assert not Path(path).exists()
    assert decoy.read_bytes() == b"unrelated temporary file"


def test_page_ingest_rejects_unrelated_temp_file(media_dirs, monkeypatch):
    from autoyou_agents.page_agent.page_tool import PageTool

    _, os_temp = media_dirs
    path = os_temp / "synthetic-private.pdf"
    path.write_bytes(b"synthetic document")
    tool = PageTool(base_url="http://127.0.0.1:18067")
    monkeypatch.setattr(tool, "_http_upload_blob", lambda **_: pytest.fail("must not upload an unrelated file"))
    result = tool.add_blob_from_path(str(path), mimetype="application/pdf")
    assert not result["success"]
    assert "outside allowed media directories" in result["error"]


def test_assistant_reply_paths_cannot_bypass_attachment_ownership(media_dirs):
    from shared import conversation_media_store, media_messaging

    _, os_temp = media_dirs
    path = os_temp / "synthetic-private.png"
    path.write_bytes(b"synthetic private image")
    attachment = {"path": str(path), "filename": path.name, "mimetype": "image/png"}
    assert media_messaging.load_attachment_bytes(attachment)[0] is None
    assert media_messaging.inline_attachment_for_client(attachment) is None
    assert conversation_media_store._attachment_bytes(attachment, trusted_path=True) is None


def test_configured_owned_output_under_shared_ancestor_still_works(media_dirs):
    from shared.content_library import configure_output_root, output_dir

    runtime, _ = media_dirs
    configure_output_root(str(runtime / "Shared" / "outputs"))
    path = output_dir("media_generation_agent") / "synthetic.png"
    path.write_bytes(b"synthetic owned image")
    assert gateway.is_allowed_attachment_path(str(path))
    from shared.media_messaging import load_attachment_bytes
    assert load_attachment_bytes({"path": str(path)}) == (b"synthetic owned image", None)


def test_broad_output_and_recording_settings_do_not_authorize_unrelated_files(media_dirs, monkeypatch):
    import server
    from shared.content_library import configure_output_root, output_dir
    from shared.recording_content import publish_recording

    runtime, _ = media_dirs
    # A legitimately broad existing destination must not recreate the temp bypass.
    configure_output_root(str(runtime))
    cfg = server._default_config()
    cfg["video_call"]["recording_dir"] = str(runtime)
    monkeypatch.setattr(server.STATE, "config", cfg)
    unrelated = runtime / "synthetic-private.txt"
    unrelated.write_bytes(b"must not be forwarded")
    assert not gateway.is_allowed_attachment_path(str(unrelated))
    uploaded = output_dir("files_agent") / "synthetic-upload.txt"
    uploaded.write_bytes(b"synthetic owned upload")
    assert gateway.is_allowed_attachment_path(str(uploaded))
    # A finalized recording is individually registered, even in a custom folder.
    video = runtime / "synthetic-finalized.mp4"
    video.write_bytes(b"synthetic finalized video")
    assert not gateway.is_allowed_attachment_path(str(video))
    assert publish_recording(video, kind="video")
    assert gateway.is_allowed_attachment_path(str(video))


def test_broad_conversation_override_accepts_only_archived_files(media_dirs, monkeypatch):
    from shared import conversation_media_store

    runtime, _ = media_dirs
    monkeypatch.setenv(conversation_media_store.CONVERSATION_MEDIA_DIR_ENV, str(runtime))
    unrelated = runtime / "synthetic-private.png"
    unrelated.write_bytes(b"synthetic private image")
    assert not gateway.is_allowed_attachment_path(str(unrelated))
    saved = conversation_media_store.store_media_bytes(b"synthetic archived image",
        user_id="synthetic-user", session_id="synthetic-session", filename="synthetic.png", mimetype="image/png")
    assert gateway.is_allowed_attachment_path(saved["storage_path"])
    assert conversation_media_store.read_media_bytes(saved["storage_path"]) == b"synthetic archived image"
