# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-534454206164647265737320-1deef658619941bf82fb04a7

"""No test may install a finite fake clock on the shared `time` module.

`some_module.time` is not a copy of the `time` module - it *is* the module, the
same object every importer holds. So::

    monkeypatch.setattr(some_module.time, "monotonic", lambda: next(values))

replaces `time.monotonic` for the whole process, including every thread. Any
asyncio event loop running at that moment calls it on each tick, drains the
iterator, and dies with `StopIteration` somewhere else entirely.

That is not hypothetical. `test_running_training_job_escalates_a_halt_to_kill_
after_the_grace_period` failed exactly this way, and only when run alongside the
tests that leave a managed frontend server running - which is why it looked like
a timing flake and passed every time it was run alone. The captured logs showed
two unrelated agent backends crashing inside the same test.

The fix in both cases was a module-level seam - `_monotonic = time.monotonic` -
that a test can replace without touching anything else in the process.

Scope: this forbids the pattern that *crashes*, a finite iterator behind a
globally-patched clock. Patching `time.sleep` or `time.time` globally is also
untidy - a no-op `sleep` turns every background retry loop into a hot spin for
the duration - but it degrades rather than explodes, and there are enough
existing sites that banning it belongs in its own change.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-534454206164647265737320-1deef658619941bf82fb04a7"


import pathlib
import re
from typing import List

import pytest

pytestmark = pytest.mark.server

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TEST_ROOTS = (
    REPO_ROOT / "tests",
    REPO_ROOT / "clients" / "python" / "tests",
    REPO_ROOT / "autoyou_lite" / "tests",
)

#: `setattr(<anything>.time, "<clock>", ...)` - a patch that lands on the shared
#: module rather than on the module under test.
_GLOBAL_CLOCK_PATCH = re.compile(
    r"setattr\(\s*[\w.]+\.time\s*,\s*[\"'](monotonic|time|perf_counter)[\"']\s*,(?P<value>[^\n]*)"
)



def _can_raise_stop_iteration(value: str) -> bool:
    """True for a bare `next(it)`, false for `next(it, default)`.

    The default form cannot raise, so it degrades to a frozen clock rather than
    crashing an unrelated event loop - untidy, but not the failure this rule
    exists to prevent. `video_call_manager` uses that form deliberately.
    """
    for match in re.finditer(r"next\(", value):
        depth, index = 1, match.end()
        while index < len(value) and depth:
            if value[index] == "(":
                depth += 1
            elif value[index] == ")":
                depth -= 1
                if depth == 0:
                    break
            elif value[index] == "," and depth == 1:
                break
            index += 1
        else:
            return True
        if index < len(value) and value[index] == ")":
            return True
    return False


def python_test_files() -> List[pathlib.Path]:
    found: List[pathlib.Path] = []
    for root in TEST_ROOTS:
        if root.is_dir():
            found.extend(sorted(root.rglob("test_*.py")))
    return found


def test_no_finite_fake_clock_is_installed_globally():
    """A `next(...)` behind a globally patched clock is the crash pattern."""
    offenders: List[str] = []
    for path in python_test_files():
        if path.name == pathlib.Path(__file__).name:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        for match in _GLOBAL_CLOCK_PATCH.finditer(source):
            if not _can_raise_stop_iteration(match.group("value")):
                continue
            line = source.count("\n", 0, match.start()) + 1
            offenders.append(
                f"{path.relative_to(REPO_ROOT).as_posix()}:{line}: "
                f"finite fake `time.{match.group(1)}` installed process-wide"
            )

    assert not offenders, (
        "a finite fake clock on the shared `time` module will be drained by any "
        "running asyncio loop and raise StopIteration in an unrelated test.\n"
        "Add a module-level seam (`_monotonic = time.monotonic`) and patch "
        "that instead:\n  " + "\n  ".join(offenders)
    )


def test_the_rule_recognises_the_pattern_it_was_written_for():
    """Both directions, so a clean sweep means something."""
    crashing = 'monkeypatch.setattr(bootstrap.time, "monotonic", lambda: next(values))'
    match = _GLOBAL_CLOCK_PATCH.search(crashing)
    assert match is not None and "next(" in match.group("value")

    # A constant is untidy but cannot raise StopIteration.
    constant = 'monkeypatch.setattr(server.time, "monotonic", lambda: 100.0)'
    match = _GLOBAL_CLOCK_PATCH.search(constant)
    assert match is not None and "next(" not in match.group("value")

    # A module seam is the fix, and must not be flagged.
    seam = 'monkeypatch.setattr(fine_tuning_tool, "_monotonic", lambda: next(values))'
    assert _GLOBAL_CLOCK_PATCH.search(seam) is None


def test_the_two_modules_that_had_this_bug_still_expose_their_seams():
    """Deleting a seam would send someone back to patching the global module."""
    for relative, name in (
        ("autoyou_agents/fine_tuning_agent/fine_tuning_tool.py", "_monotonic"),
        ("autoyou_agents/fine_tuning_agent/fine_tuning_tool.py", "_sleep"),
        ("scripts/bootstrap_autoyou.py", "_monotonic"),
        ("server.py", "_startup_clock"),
    ):
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        assert re.search(rf"^{name} = time\.", source, re.MULTILINE), (
            f"{relative} no longer defines the {name} seam"
        )


def test_the_seams_are_actually_used():
    """A seam nothing calls is decoration; the loop must go through it."""
    fine_tuning = (
        REPO_ROOT / "autoyou_agents" / "fine_tuning_agent" / "fine_tuning_tool.py"
    ).read_text(encoding="utf-8")
    supervision = fine_tuning.split("cancellation_sent = False")[1].split("_finish_logged_process")[0]
    assert "_monotonic()" in supervision, "the cancellation loop bypasses its seam"
    assert "time.monotonic()" not in supervision, "the loop still calls the global clock"

    bootstrap = (REPO_ROOT / "scripts" / "bootstrap_autoyou.py").read_text(encoding="utf-8")
    assert "now = _monotonic()" in bootstrap

    # The startup status reads the clock in two functions - the updater that
    # stamps `started_at`, and the payload that measures against it. A seam
    # covering only one of them leaves the test patching the global module for
    # the other, which is how this one was originally written.
    server = (REPO_ROOT / "server.py").read_text(encoding="utf-8")
    updater = server.split("def _update_startup_status(")[1].split("def _startup_status_payload(")[0]
    payload = server.split("def _startup_status_payload(")[1].split("build_instance_runtime_status()")[0]
    assert "_startup_clock()" in updater and "time.time()" not in updater
    assert "_startup_clock()" in payload and "time.time()" not in payload
