from __future__ import annotations

import os

import pytest

from shared.path_safety import contained_path


@pytest.fixture
def media_dir(tmp_path):
    root = tmp_path / "media"
    (root / "images").mkdir(parents=True)
    (root / "images" / "a.png").write_bytes(b"synthetic")
    (tmp_path / "outside.txt").write_text("synthetic", encoding="utf-8")
    return root


def test_names_inside_the_directory_resolve(media_dir) -> None:
    assert contained_path(media_dir, "images", "a.png") == (media_dir / "images" / "a.png").resolve()
    assert contained_path(media_dir, "images/../images/a.png") == (media_dir / "images" / "a.png").resolve()


@pytest.mark.parametrize("name", ["..", "../outside.txt", "images/../../outside.txt", "", ".", "/etc/hosts"])
def test_names_that_leave_the_directory_are_refused(media_dir, name: str) -> None:
    assert contained_path(media_dir, name) is None


def test_symlinks_out_of_the_directory_are_refused(media_dir, tmp_path) -> None:
    link = media_dir / "images" / "escape"
    try:
        os.symlink(tmp_path / "outside.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable here")
    assert contained_path(media_dir, "images", "escape") is None
