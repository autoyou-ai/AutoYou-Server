"""Opt-in real dependency checks; no operator package installation or application."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


def probe(tmp_path: Path, code: str) -> None:
    location = os.environ.get("AUTOYOU_PATCHED_DEPENDENCY_ROOT")
    if not location:
        pytest.skip("Set AUTOYOU_PATCHED_DEPENDENCY_ROOT to the isolated qualified wheels")
    packages = Path(location).resolve()
    assert packages.is_dir()
    env = dict(os.environ, AUTOYOU_TEST_ROOT=str(tmp_path), PYTHONDONTWRITEBYTECODE="1",
               AUTOYOU_DISABLE_DOTENV="1", PYTHON_DOTENV_DISABLED="1")
    setup = "import sys; sys.path.insert(0, sys.argv[1]);\n"
    result = subprocess.run([sys.executable, "-I", "-c", setup + code, str(packages)],
                            env=env, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_multidict_view_union_and_subtraction_release_operand_values(tmp_path):
    probe(tmp_path, '''
import gc, weakref, multidict
assert multidict.__version__ == "6.9.1"
class SyntheticValue: pass
for subtract in (False, True):
    value = SyntheticValue()
    reference = weakref.ref(value)
    operand = {("synthetic-key", value)}
    view = multidict.CIMultiDict().items()
    result = view - operand if subtract else operand | view
    del value, operand, view, result
    gc.collect()
    assert reference() is None, "Items-view operand retains a value after cleanup"
''')


def test_fsspec_keeps_reference_templates_and_rejects_object_introspection(tmp_path):
    probe(tmp_path, '''
import os
from pathlib import Path
import fsspec
from jinja2.exceptions import SecurityError
assert fsspec.__version__ == "2026.6.0"
root = Path(os.environ["AUTOYOU_TEST_ROOT"])
source = root / "synthetic.txt"
source.write_bytes(b"synthetic payload")
fs = fsspec.filesystem("reference", fo={"version": 1,
    "templates": {"root": root.as_posix()},
    "refs": {"synthetic": ["{{root}}/synthetic.txt", 0, 9]}})
assert fs.cat("synthetic") == b"synthetic"
try:
    fsspec.filesystem("reference", fo={"version": 1,
        "templates": {"root": "{{ ''.__class__.__name__ }}"},
        "refs": {"synthetic": ["{{root}}/synthetic.txt", 0, 1]}})
except SecurityError:
    pass
else:
    raise AssertionError("Reference templates permit object introspection")
''')
