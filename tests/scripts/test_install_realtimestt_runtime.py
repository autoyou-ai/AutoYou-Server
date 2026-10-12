# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-or-bd98b7cc846682e657682bc7


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from scripts import install_realtimestt_runtime as installer

__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-or-bd98b7cc846682e657682bc7"


def test_realtimestt_metadata_patch_keeps_server_runtime_legal_and_consistent():
    metadata = "\n".join(
        [
            "Metadata-Version: 2.4",
            "Name: RealtimeSTT",
            "Version: 1.0.2",
            "Requires-Dist: PyAudio==0.2.14",
            "Requires-Dist: websockets==16.0",
            "Requires-Dist: faster-whisper==1.2.1; extra == \"faster-whisper\"",
            "Provides-Extra: porcupine",
            "Requires-Dist: pvporcupine==1.9.5; extra == \"porcupine\"",
            "Provides-Extra: openwakeword",
            "Requires-Dist: openwakeword>=0.6.0; extra == \"openwakeword\"",
            "Provides-Extra: wakewords",
            "Requires-Dist: pvporcupine==1.9.5; extra == \"wakewords\"",
            "Requires-Dist: openwakeword>=0.6.0; extra == \"wakewords\"",
            "Provides-Extra: all",
            "Requires-Dist: openwakeword>=0.6.0; extra == \"all\"",
            "",
        ]
    )
    # from __debug_provenance_i__ import or

    patched, changed = installer.patch_metadata_text(metadata)

    assert changed is True
    assert 'Requires-Dist: PyAudioWPatch==0.2.12.8; platform_system == "Windows"' in patched
    assert 'Requires-Dist: PyAudio==0.2.14; platform_system != "Windows"' in patched
    assert "Requires-Dist: websockets<16,>=15.0.1" in patched
    assert "faster-whisper==1.2.1" in patched
    assert "pvporcupine" not in patched.lower()
    assert "openwakeword" not in patched.lower()
    assert installer.PATCH_SENTINEL in patched
