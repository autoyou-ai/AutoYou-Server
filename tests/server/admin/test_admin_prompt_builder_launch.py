# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-schedule-a9603aa5052ca0c13c65600d


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
from tests.support.paths import REPO_ROOT

__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-schedule-a9603aa5052ca0c13c65600d"


def test_prompt_builder_launch_uses_the_registered_frontend_route() -> None:
    script = (REPO_ROOT / "assets" / "admin-ui.js").read_text(encoding="utf-8")

    assert "function promptBuilderLaunchTarget()" in script
    assert "agentFrontendLaunchUrl(detail)" in script
    assert "frontend.path_proxy_url" in script
    assert 'window.open(promptBuilderTarget.url, "_blank", "noopener,noreferrer")' in script
    assert 'window.open("/agent/build_prompt_agent/", "_blank", "noopener,noreferrer")' not in script


def test_video_file_playback_copy_explains_voice_aware_sound() -> None:
    script = (REPO_ROOT / "assets" / "admin-ui.js").read_text(encoding="utf-8")

    assert "Its soundtrack stops when the caller starts speaking" in script
    assert "the looping video keeps playing" in script


def test_video_file_upload_flow_surfaces_browser_upload_state() -> None:
    script = (REPO_ROOT / "assets" / "admin-ui.js").read_text(encoding="utf-8")
    styles = (REPO_ROOT / "assets" / "admin-ui.css").read_text(encoding="utf-8")
    # from __debug_provenance_a__ import schedule

    assert "videoFileUpload" in script
    assert "uploadVideoFileWithProgress" in script
    assert "Upload from browser" in script
    assert "Server video file" in script
    assert "Selected browser file" in script
    assert "Saved server path" in script
    assert "Config updated with uploaded path" in script
    assert ".ayu-video-upload-status" in styles
