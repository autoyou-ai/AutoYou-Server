# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License. See LICENSE.
"""Bounded coverage of the stock English TTS path, not all NLTK APIs."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_stock_english_tts_uses_fixed_resources_for_speech_text(tmp_path):
    if any(importlib.util.find_spec(name) is None for name in ("nltk", "g2p_en")):
        pytest.skip("Requires the optional voice dependencies")

    # Isolate NLTK's module caches and path authorizations from other tests.
    # The child gets only synthetic resources; downloading is forbidden.
    child = r'''
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

resource_root = Path(sys.argv[1]) / "nltk_data"
tagger_root = resource_root / "taggers" / "averaged_perceptron_tagger_eng"
tagger_root.mkdir(parents=True)
for name, value in (("weights", {}), ("tagdict", {"hello": "NN"}), ("classes", ["NN"])):
    (tagger_root / ("averaged_perceptron_tagger_eng." + name + ".json")).write_text(json.dumps(value), encoding="utf-8")
# g2p_en checks for these legacy resource archives at import time. They are
# discovery sentinels only; this test never loads a real corpus or checkpoint.
for category, name in (("taggers", "averaged_perceptron_tagger"), ("corpora", "cmudict")):
    archive = resource_root / category / (name + ".zip")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(name + "/", "")

import nltk
from nltk.classify import maxent
from nltk.parse.transitionparser import TransitionParser
from nltk.tag import perceptron

nltk.data.path[:] = [str(resource_root)]
nltk.tag._get_tagger.cache_clear()

def forbidden(*args, **kwargs):
    raise AssertionError("TTS reached an affected artifact API or attempted a download")

nltk.download = forbidden
TransitionParser.train = forbidden
TransitionParser.parse = forbidden
perceptron.AveragedPerceptron.save = forbidden
perceptron.AveragedPerceptron.load = forbidden
perceptron.PerceptronTagger.save_to_json = forbidden
maxent.save_maxent_params = forbidden

opened = []
real_open = perceptron.open_datafile
def observed_open(location, name, *args, **kwargs):
    assert Path(location.path).resolve() == tagger_root.resolve()
    opened.append(name)
    return real_open(location, name, *args, **kwargs)
perceptron.open_datafile = observed_open

from g2p_en import G2p
spec = importlib.util.spec_from_file_location("synthetic_tts_frontend", sys.argv[2])
frontend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frontend)

# Keep real frontend splitting, G2p preprocessing, pos_tag, and resource
# loading. Substitute phoneme prediction so no model or corpus is needed.
g2p = G2p.__new__(G2p)
g2p.cmu = {"hello": [["HH", "EH", "L", "OW"]]}
g2p.homograph2features = {}
g2p.predict = lambda word: ["HH", "EH", "L", "OW"]
for text in ("../../outside/model.json", r"C:\outside\model.json", "Hello friend"):
    phones = frontend.get_eng_phoneme(text, g2p, {}, pad_sos_eos=False)
    assert "[HH]" in phones
assert set(opened) == {"averaged_perceptron_tagger_eng." + name + ".json" for name in ("weights", "tagdict", "classes")}
print("fixed English resources only; artifact APIs and downloads unused")
'''
    repo_root = Path(__file__).resolve().parents[3]
    environment = dict(os.environ)
    environment["NLTK_DATA"] = str(tmp_path / "nltk_data")
    environment["AUTOYOU_TEST_ROOT"] = str(tmp_path)
    environment["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    result = subprocess.run(
        [sys.executable, "-c", child, str(tmp_path), str(repo_root / "vendor/emotivoice/frontend_en.py")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "artifact APIs and downloads unused" in result.stdout
