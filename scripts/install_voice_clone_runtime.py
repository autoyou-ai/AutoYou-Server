#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Install the voice-clone runtime: a Python environment of its own for Chatterbox.

The custom voice TTS provider clones the owner's voice with Chatterbox
(Resemble AI, MIT). Chatterbox pins transformers, diffusers and torch releases
the server does not use, so it runs in this separate environment and the server
talks to ``shared/voice_clone_worker.py`` over a pipe.

Steps: create a venv, put in a PyTorch build for this machine, install
``requirements/voice-clone.txt`` under a constraint that keeps that PyTorch,
install ``chatterbox-tts`` without its dependencies, then run the worker's
self-check.

PyTorch from PyPI is CPU-only on Windows. For a GPU, pass the PyTorch wheel
index for your hardware (for NVIDIA, for example,
``--torch-index-url https://download.pytorch.org/whl/cu128``), or reuse a venv
that already has a GPU build with ``--reuse-torch-from`` (same Python version).

The default location is found automatically by AutoYou. For any other
``--target``, point AUTOYOU_VOICE_CLONE_PYTHON at the printed interpreter.

The first cloned voice build downloads the 3 GB model from Hugging Face.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import argparse
import base64
import csv
import hashlib
import importlib.metadata
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS_FILE = REPO_ROOT / "requirements" / "voice-clone.txt"
WORKER_SCRIPT = REPO_ROOT / "shared" / "voice_clone_worker.py"
CHATTERBOX_SPEC = "chatterbox-tts==0.1.7"
RUNTIME_DIRNAME = "voice_clone_runtime"
SUPPORTED_PYTHONS = ((3, 10), (3, 11), (3, 12), (3, 13))
PERTH_VERSION = "1.0.1"
PERTH_RESOURCES_OLD = (
    'from pkg_resources import resource_filename\n'
    'PREPACKAGED_MODELS_DIR = resource_filename(__name__, "pretrained")\n'
)
PERTH_RESOURCES_NEW = (
    'from pathlib import Path\n'
    'PREPACKAGED_MODELS_DIR = str(Path(__file__).resolve().parent / "pretrained")\n'
)


def patch_perth_resources(site_packages: Path) -> bool:
    """Backport the resource lookup only, retaining Perth's model and watermark.

    Work only in the explicitly selected runtime. Never import Perth or fall
    back to a distribution inherited from another Python environment.
    """
    root = site_packages.resolve()
    matches = [dist for dist in importlib.metadata.distributions(path=[str(root)])
               if dist.metadata.get("Name", "").lower().replace("_", "-") == "resemble-perth"]
    if len(matches) != 1 or matches[0].version != PERTH_VERSION:
        raise RuntimeError(f"Expected resemble-perth {PERTH_VERSION} in the voice runtime")
    dist = matches[0]
    package_file = root / "perth/perth_net/__init__.py"
    records = [root / item for item in (dist.files or ()) if str(item).replace("\\", "/").endswith(".dist-info/RECORD")]
    if len(records) != 1:
        raise RuntimeError("Perth's wheel RECORD is missing or ambiguous")
    record = records[0]
    provenance = record.parent / "AUTOYOU-PATCHES.json"
    for path in (package_file, record, provenance):
        if not path.resolve().is_relative_to(root):
            raise RuntimeError("Refusing to patch Perth outside the selected runtime")
    original = package_file.read_text(encoding="utf-8")
    suffix = '\nfrom .perth_net_implicit.perth_watermarker import PerthImplicitWatermarker\n'
    if original not in (PERTH_RESOURCES_OLD + suffix, PERTH_RESOURCES_NEW + suffix):
        raise RuntimeError("Perth's resource module differs from the reviewed 1.0.1 source")
    patched = (PERTH_RESOURCES_NEW + suffix).encode("utf-8")
    evidence = (json.dumps({
        "upstream": "resemble-perth", "version": PERTH_VERSION,
        "patch": "stdlib-resource-path-2026-11-07", "license": "MIT",
        "file": "perth/perth_net/__init__.py",
        "before_sha256": hashlib.sha256((PERTH_RESOURCES_OLD + suffix).encode()).hexdigest(),
        "after_sha256": hashlib.sha256(patched).hexdigest(),
        "scope": "Replace pkg_resources lookup only; model files and watermark code unchanged",
    }, indent=2) + "\n").encode("utf-8")
    updates = {package_file.relative_to(root).as_posix(): patched,
               provenance.relative_to(root).as_posix(): evidence}
    rows = list(csv.reader(io.StringIO(record.read_text(encoding="utf-8"))))
    rows = [row for row in rows if row and row[0] not in updates]
    for name, data in updates.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        rows.append([name, "sha256=" + digest, str(len(data))])
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(rows)
    updates[record.relative_to(root).as_posix()] = output.getvalue().encode("utf-8")
    changed = False
    for name, data in updates.items():
        path = root / name
        if not path.exists() or path.read_bytes() != data:
            # An interrupted install must not leave a truncated Python module
            # which the strict source check cannot safely recognize next time.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".autoyou-patch-", delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(data)
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            changed = True
    return changed


def default_target() -> Path:
    """Where AutoYou looks for the runtime when AUTOYOU_VOICE_CLONE_PYTHON is unset."""
    sys.path.insert(0, str(REPO_ROOT))
    try:
        from shared.platform_runtime import get_mutable_data_dir

        return get_mutable_data_dir("AutoYou", anchor=REPO_ROOT / "server.py") / RUNTIME_DIRNAME
    finally:
        sys.path.remove(str(REPO_ROOT))


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _python_version(python: str) -> tuple[int, int]:
    output = subprocess.run(
        [python, "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    return int(output[0]), int(output[1])


def _site_packages(python: str) -> Path:
    output = subprocess.run(
        [python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return Path(output)


def _installed_versions(python: str, names: Sequence[str]) -> dict[str, str]:
    code = (
        "import json, importlib.metadata as m\n"
        "out = {}\n"
        f"for name in {list(names)!r}:\n"
        "    try:\n"
        "        out[name] = m.version(name)\n"
        "    except m.PackageNotFoundError:\n"
        "        pass\n"
        "print(json.dumps(out))\n"
    )
    output = subprocess.run([python, "-c", code], check=True, capture_output=True, text=True).stdout
    return json.loads(output.strip().splitlines()[-1])


def pip_command(python: str, *args: str) -> List[str]:
    return [python, "-m", "pip", "install", "--disable-pip-version-check", *args]


def _run(command: Sequence[str], *, dry_run: bool) -> None:
    print("+ " + " ".join(str(part) for part in command), flush=True)
    if not dry_run:
        subprocess.run(list(command), check=True)


def install(
    *,
    target: Path,
    base_python: str,
    torch_index_url: str = "",
    reuse_torch_from: Optional[Path] = None,
    skip_torch: bool = False,
    dry_run: bool = False,
) -> Path:
    if not REQUIREMENTS_FILE.is_file():
        raise SystemExit(f"Missing {REQUIREMENTS_FILE}")
    version = _python_version(base_python)
    if version not in SUPPORTED_PYTHONS:
        raise SystemExit(f"Python {version[0]}.{version[1]} is not supported; use 3.10 to 3.13.")

    venv_dir = target / ".venv"
    python = str(venv_python(venv_dir))
    if not venv_python(venv_dir).is_file():
        _run([base_python, "-m", "venv", str(venv_dir)], dry_run=dry_run)
    if not dry_run:
        _run(pip_command(python, "--upgrade", "pip"), dry_run=dry_run)

    if reuse_torch_from is not None:
        other_python = venv_python(reuse_torch_from)
        if not other_python.is_file():
            raise SystemExit(f"No Python found in {reuse_torch_from}")
        if _python_version(str(other_python)) != version:
            raise SystemExit("--reuse-torch-from must use the same Python version as the new runtime.")
        borrowed = _site_packages(str(other_python))
        print(f"Reusing PyTorch from {borrowed}")
        if not dry_run:
            # Appended after the runtime's own site-packages, so packages installed
            # here (transformers, diffusers) win over the other venv's copies.
            (_site_packages(python) / "zz_voice_clone_reuse_torch.pth").write_text(f"{borrowed}\n", encoding="utf-8")
    elif not skip_torch:
        torch_args = ["torch", "torchaudio"]
        if torch_index_url:
            torch_args += ["--index-url", torch_index_url]
        _run(pip_command(python, *torch_args), dry_run=dry_run)

    test_root = os.environ.get("AUTOYOU_TEST_ROOT")
    if test_root:
        Path(test_root).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="autoyou-voice-clone-", dir=test_root) as scratch:
        constraints = Path(scratch) / "constraints.txt"
        if dry_run:
            pinned = {"torch": "<installed>", "numpy": "<installed>"}
        else:
            pinned = _installed_versions(python, ["torch", "torchaudio", "numpy"])
            if "torch" not in pinned:
                raise SystemExit("PyTorch is not importable in the runtime; check --torch-index-url or --reuse-torch-from.")
            if "torchaudio" not in pinned:
                # Chatterbox only uses torchaudio's pure-Python resampler and Kaldi
                # fbank, so a build without the native extension is enough.
                _run(pip_command(python, "--no-deps", "torchaudio"), dry_run=dry_run)
                pinned = _installed_versions(python, ["torch", "torchaudio", "numpy"])
            constraints.write_text("".join(f"{name}=={value}\n" for name, value in pinned.items()), encoding="utf-8")
        print("Keeping " + ", ".join(f"{name} {value}" for name, value in pinned.items()))
        _run(pip_command(python, "-r", str(REQUIREMENTS_FILE), "-c", str(constraints)), dry_run=dry_run)
        # A reused Torch environment's .pth may also expose Perth. Pip could
        # otherwise consider that borrowed copy satisfied and leave no local
        # copy to patch. Ignore it without uninstalling or editing the donor.
        _run(pip_command(python, "--ignore-installed", "--no-deps", f"resemble-perth=={PERTH_VERSION}"), dry_run=dry_run)
        if not dry_run:
            site_packages = _site_packages(python).resolve()
            if not site_packages.is_relative_to(venv_dir.resolve()):
                raise RuntimeError("Refusing to patch packages outside the voice-clone venv")
            patch_perth_resources(site_packages)
        _run(pip_command(python, "--no-deps", CHATTERBOX_SPEC), dry_run=dry_run)

    if not dry_run:
        completed = subprocess.run([python, "-I", str(WORKER_SCRIPT), "--probe"], capture_output=True, text=True)
        lines = completed.stdout.strip().splitlines()
        report = json.loads(lines[-1]) if lines else {}
        print(json.dumps(report, indent=2))
        if not report.get("available"):
            raise SystemExit("The voice-clone runtime failed its self-check (see above).")
    return Path(python)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=Path, default=None, help="Runtime folder (default: AutoYou's own location).")
    parser.add_argument("--python", default=sys.executable, help="Base Python 3.10-3.13 used to create the venv.")
    parser.add_argument("--torch-index-url", default="", help="PyTorch wheel index for this machine's GPU.")
    parser.add_argument("--reuse-torch-from", type=Path, default=None, help="A venv whose PyTorch build to reuse.")
    parser.add_argument("--skip-torch", action="store_true", help="PyTorch is already installed in the target venv.")
    parser.add_argument("--dry-run", action="store_true", help="Print the steps without running them.")
    args = parser.parse_args(argv)

    target = (args.target or default_target()).resolve()
    python = install(
        target=target,
        base_python=args.python,
        torch_index_url=args.torch_index_url,
        reuse_torch_from=args.reuse_torch_from.resolve() if args.reuse_torch_from else None,
        skip_torch=args.skip_torch,
        dry_run=args.dry_run,
    )
    if args.target is not None:
        print(f"Set AUTOYOU_VOICE_CLONE_PYTHON={python} so AutoYou uses this runtime.")
    else:
        print(f"Installed at {python}; AutoYou finds it automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
