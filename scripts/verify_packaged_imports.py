# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Run packaged import checks with copied resources and disposable application state."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SERVER = str(Path(__file__).resolve().parents[1])
if SERVER not in sys.path:
    sys.path.insert(0, SERVER)
from shared.process_lifecycle import force_kill_process_tree, process_spawn_kwargs


def verify(executable: Path, arguments: list[str], *, work_root: Path, timeout: int) -> int:
    executable = executable.resolve(strict=True)
    resources = executable.parent
    work_root = work_root.resolve()
    if work_root.is_relative_to(resources):
        raise ValueError("Verification state must remain outside the package")
    work_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="packaged-import-", dir=work_root) as temporary:
        state = Path(temporary)
        copied = state / "resources"
        cloned = sys.platform == "darwin" and subprocess.run(
            ["/bin/cp", "-cR", str(resources), str(copied)], capture_output=True).returncode == 0
        if not cloned:
            if copied.exists():
                shutil.rmtree(copied)
            shutil.copytree(resources, copied, symlinks=True)
        env = os.environ.copy()
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(name, None)
        env.update(AUTOYOU_TEST_ROOT=str(state), AUTOYOU_RUNTIME_ROOT=str(state),
            AUTOYOU_PACKAGED_RESOURCES_ROOT=str(copied), PYTHON_KEYRING_BACKEND="keyring.backends.null.Keyring",
            PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
            AUTOYOU_SKIP_STARTUP_SERVICES="1")
        if env.get("DYLD_LIBRARY_PATH"):
            env["DYLD_LIBRARY_PATH"] = env["DYLD_LIBRARY_PATH"].replace(str(resources), str(copied))
        process = subprocess.Popen([str(copied / executable.name), *arguments], cwd=state,
                                   env=env, **process_spawn_kwargs(hide_window=True))
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            force_kill_process_tree(process.pid, process_group=True)
            process.wait(timeout=10)
            if isinstance(error, KeyboardInterrupt):
                raise
            print("Packaged import verification timed out", file=sys.stderr)
            return 124


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    return verify(args.executable, arguments, work_root=args.work_root, timeout=args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
