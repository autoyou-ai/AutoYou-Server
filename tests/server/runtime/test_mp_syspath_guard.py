# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-746f20706179203130252061-016787524bea38ff676ca374


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-746f20706179203130252061-016787524bea38ff676ca374"

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import multiprocessing.spawn as mp_spawn

import shared.mp_syspath_guard as guard


def _make_cv2_like_package(root, name="cv2"):
    """Reproduce the layout that breaks spawned children.

    ``site-packages/cv2`` is a package (has ``__init__.py``) that also contains
    a ``typing`` subpackage, so putting the cv2 directory itself on sys.path
    makes ``import typing`` resolve to ``cv2/typing/__init__.py``.
    """
    pkg = root / name
    (pkg / "typing").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "typing" / "__init__.py").write_text("", encoding="utf-8")
    return pkg


def test_is_package_directory_identifies_shadowing_entries(tmp_path):
    pkg = _make_cv2_like_package(tmp_path)
    plain = tmp_path / "site-packages"
    plain.mkdir()
    a_file = tmp_path / "notadir.txt"
    a_file.write_text("", encoding="utf-8")

    assert guard._is_package_directory(str(pkg)) is True
    assert guard._is_package_directory(str(plain)) is False
    assert guard._is_package_directory(str(a_file)) is False
    assert guard._is_package_directory(str(tmp_path / "missing")) is False
    assert guard._is_package_directory("") is False
    assert guard._is_package_directory(None) is False


def test_sanitize_child_sys_path_drops_only_package_dirs(tmp_path):
    pkg = _make_cv2_like_package(tmp_path)
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    paths = [str(repo_root), str(pkg), str(site_packages), ""]

    assert guard.sanitize_child_sys_path(paths) == [
        str(repo_root),
        str(site_packages),
        "",
    ]


def test_guard_strips_shadowing_entry_from_child_preparation_data(tmp_path, monkeypatch):
    pkg = _make_cv2_like_package(tmp_path)
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    polluted = [str(site_packages), str(pkg)]

    def fake_original(name, *args, **kwargs):
        return {"name": name, "sys_path": list(polluted), "dir": str(tmp_path)}

    monkeypatch.setattr(mp_spawn, "get_preparation_data", fake_original)
    monkeypatch.setattr(guard, "_installed", False)

    assert guard.install_multiprocessing_syspath_guard() is True

    data = mp_spawn.get_preparation_data("child")

    assert data["sys_path"] == [str(site_packages)]
    # Untouched keys pass through unchanged.
    assert data["name"] == "child"
    assert data["dir"] == str(tmp_path)


def test_guard_leaves_clean_sys_path_alone(tmp_path, monkeypatch):
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    clean = [str(site_packages), ""]

    def fake_original(name, *args, **kwargs):
        return {"name": name, "sys_path": list(clean)}

    monkeypatch.setattr(mp_spawn, "get_preparation_data", fake_original)
    monkeypatch.setattr(guard, "_installed", False)
    guard.install_multiprocessing_syspath_guard()

    assert mp_spawn.get_preparation_data("child")["sys_path"] == clean


def test_guard_installs_once_and_does_not_double_wrap(monkeypatch):
    calls = []

    def fake_original(name, *args, **kwargs):
        calls.append(name)
        return {"name": name, "sys_path": []}

    monkeypatch.setattr(mp_spawn, "get_preparation_data", fake_original)
    monkeypatch.setattr(guard, "_installed", False)

    assert guard.install_multiprocessing_syspath_guard() is True
    first = mp_spawn.get_preparation_data

    # A second call while the flag is set is a no-op...
    assert guard.install_multiprocessing_syspath_guard() is True
    assert mp_spawn.get_preparation_data is first

    # ...and so is one that re-checks the already-marked wrapper.
    monkeypatch.setattr(guard, "_installed", False)
    assert guard.install_multiprocessing_syspath_guard() is True
    assert mp_spawn.get_preparation_data is first

    mp_spawn.get_preparation_data("child")
    assert calls == ["child"]


def test_guard_survives_missing_get_preparation_data(monkeypatch):
    monkeypatch.delattr(mp_spawn, "get_preparation_data", raising=False)
    monkeypatch.setattr(guard, "_installed", False)

    assert guard.install_multiprocessing_syspath_guard() is False


def test_guard_returns_original_data_when_sanitization_raises(tmp_path, monkeypatch):
    def fake_original(name, *args, **kwargs):
        return {"name": name, "sys_path": ["keep-me"]}

    def boom(_paths):
        raise RuntimeError("stat storm")

    monkeypatch.setattr(mp_spawn, "get_preparation_data", fake_original)
    monkeypatch.setattr(guard, "_installed", False)
    guard.install_multiprocessing_syspath_guard()
    monkeypatch.setattr(guard, "sanitize_child_sys_path", boom)

    # A guard failure must never break process startup.
    assert mp_spawn.get_preparation_data("child")["sys_path"] == ["keep-me"]
