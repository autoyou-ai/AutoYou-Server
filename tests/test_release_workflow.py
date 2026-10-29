# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""Checks for the release version helper and the published workflow set."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import shutil
from pathlib import Path

from scripts import check_release_version as release_version
from scripts import export_public_autoyou_server as exporter

ROOT = Path(__file__).resolve().parents[1]


def _copy_version_sites(tmp_path: Path) -> Path:
    for rel in ["VERSION", *(site for site, _, _ in release_version.VERSION_SITES)]:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, target)
    return tmp_path


def test_repository_version_sites_agree():
    assert release_version.find_mismatches() == []


def test_set_version_updates_every_site_and_keeps_line_endings(tmp_path):
    root = _copy_version_sites(tmp_path)
    bat = root / "run_autoyou.bat"
    bat.write_bytes(bat.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    release_version.set_version("99.1.2", root)
    assert release_version.find_mismatches(root, expected="99.1.2") == []
    assert b"\r\n" in bat.read_bytes() and b"\r\r" not in bat.read_bytes()


def test_mismatch_is_reported(tmp_path):
    root = _copy_version_sites(tmp_path)
    (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
    problems = release_version.find_mismatches(root)
    assert len(problems) == len(release_version.VERSION_SITES)
    assert all("VERSION says 1.2.3" in problem for problem in problems)
    assert release_version.find_mismatches(root, expected="1.2.4")


def test_every_workflow_is_published():
    workflows = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / ".github" / "workflows").glob("*.yml"))
    assert ".github/workflows/release.yml" in workflows
    assert [path for path in workflows if path not in exporter.PUBLIC_GITHUB_PATHS] == []
