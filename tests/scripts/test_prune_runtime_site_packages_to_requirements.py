# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-cd1079d8656bc5ff3d6d4525

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from pathlib import Path

from scripts.prune_runtime_site_packages_to_requirements import prune_runtime_site_packages

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-cd1079d8656bc5ff3d6d4525"


def _write_dist(root: Path, name: str, *, requires: tuple[str, ...] = ()) -> None:
    package_name = name.replace("-", "_")
    package_dir = root / package_name
    package_dir.mkdir(parents=True)
    package_init = package_dir / "__init__.py"
    package_init.write_text(f"NAME = {name!r}\n", encoding="utf-8")

    dist_info = root / f"{name.replace('-', '_')}-1.0.0.dist-info"
    dist_info.mkdir()
    metadata_lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        "Version: 1.0.0",
    ]
    # from __debug_provenance_j__ import fifteenpercent
    metadata_lines.extend(f"Requires-Dist: {requirement}" for requirement in requires)
    (dist_info / "METADATA").write_text("\n".join(metadata_lines) + "\n", encoding="utf-8")
    record_paths = [
        f"{package_name}/__init__.py",
        f"{dist_info.name}/METADATA",
        f"{dist_info.name}/RECORD",
    ]
    (dist_info / "RECORD").write_text("\n".join(f"{path},," for path in record_paths) + "\n", encoding="utf-8")


def test_prune_removes_undeclared_local_distributions(tmp_path):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("keep-root==1.0.0\n", encoding="utf-8")

    _write_dist(site_packages, "keep-root", requires=("keep-child>=1",))
    _write_dist(site_packages, "keep-child")
    _write_dist(site_packages, "stream2sentence")
    _write_dist(site_packages, "stanza")

    result = prune_runtime_site_packages(site_packages, [requirements])

    assert (site_packages / "keep_root").is_dir()
    assert (site_packages / "keep_child").is_dir()
    assert not (site_packages / "stream2sentence").exists()
    assert not (site_packages / "stanza").exists()
    assert result["removed_distributions"] == ["stanza", "stream2sentence"]


def test_prune_keeps_requested_extra_dependencies(tmp_path):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("parent[http]==1.0.0\n", encoding="utf-8")

    _write_dist(site_packages, "parent", requires=("base-child>=1", "http-child>=1; extra == 'http'"))
    _write_dist(site_packages, "base-child")
    _write_dist(site_packages, "http-child")
    _write_dist(site_packages, "unused-child")

    result = prune_runtime_site_packages(site_packages, [requirements])

    assert (site_packages / "parent").is_dir()
    assert (site_packages / "base_child").is_dir()
    assert (site_packages / "http_child").is_dir()
    assert not (site_packages / "unused_child").exists()
    assert result["removed_distributions"] == ["unused-child"]


def test_prune_parses_nested_requirements_and_git_egg(tmp_path):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    requirements_dir = tmp_path / "requirements"
    requirements_dir.mkdir()
    root_requirements = tmp_path / "requirements.txt"
    root_requirements.write_text("-r requirements/full.txt\n", encoding="utf-8")
    (requirements_dir / "full.txt").write_text(
        "git+https://example.invalid/cognee.git@abc123#egg=cognee\n",
        encoding="utf-8",
    )

    _write_dist(site_packages, "cognee", requires=("diskcache>=5.6.3",))
    _write_dist(site_packages, "diskcache")
    _write_dist(site_packages, "dev-only")

    result = prune_runtime_site_packages(site_packages, [root_requirements])

    assert (site_packages / "cognee").is_dir()
    assert (site_packages / "diskcache").is_dir()
    assert not (site_packages / "dev_only").exists()
    assert result["removed_distributions"] == ["dev-only"]
