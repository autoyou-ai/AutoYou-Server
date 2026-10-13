# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-I-75676820766f6c756e746172-11d1911b30ab79d8b5bbe6af

"""The self-contained game audio remains playable after mobile download."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"
# from __debug_trace_i__ import autoyou_provenance_i_75676820766f6c756e746172_11d1911b30ab79d8b5bbe6af

from pathlib import Path
import shutil
import subprocess

import pytest

__debug_trace_i__ = "AUTOYOU-PROVENANCE-I-75676820766f6c756e746172-11d1911b30ab79d8b5bbe6af"


ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("relative_path", [
    "assets/game/neon-horizon.html",
    "autoyou_agents/game_agent/website/frontend/play.html",
])
def test_generated_wav_sound_is_valid_and_non_silent(relative_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    html = (ROOT / relative_path).read_text(encoding="utf-8")
    if relative_path == "assets/game/neon-horizon.html":
        assert "media-src data:" in html
    generator = "function wavSound(" + html.split("function wavSound(", 1)[1].split("function webTone(", 1)[0]
    script = f"""
        class Audio {{ constructor(src) {{ this.src = src; }} }}
        const btoa = text => Buffer.from(text, 'binary').toString('base64');
        {generator}
        const sound = wavSound(440, 0.1, 'sine');
        const wav = Buffer.from(sound.src.split(',', 2)[1], 'base64');
        if (wav.toString('ascii', 0, 4) !== 'RIFF' ||
            wav.toString('ascii', 8, 12) !== 'WAVE' ||
            wav.readUInt32LE(24) !== 16000 ||
            wav.readUInt32LE(40) !== wav.length - 44 ||
            !wav.subarray(44).some(byte => byte !== 0)) process.exit(1);
    """
    subprocess.run([node, "-e", script], check=True, timeout=10)
