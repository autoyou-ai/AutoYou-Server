"""The self-contained game audio remains playable after mobile download."""

from pathlib import Path
import shutil
import subprocess

import pytest


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
