# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""A sandbox may refuse a mime file that exists; startup must survive it."""

import mimetypes

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

from shared.platform_runtime import use_readable_mime_database


def _deny_file_open(monkeypatch, denied):
    actual_open = open

    def guarded_open(path, *args, **kwargs):
        if path == str(denied):
            raise PermissionError
        return actual_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", guarded_open)


def test_unreadable_known_files_are_dropped_and_guesses_still_work(tmp_path, monkeypatch):
    readable = tmp_path / "readable.types"
    readable.write_text("text/x-synthetic\t\tsynthetic\n")
    refused = tmp_path / "refused.types"
    refused.write_text("text/x-refused\t\trefused\n")
    _deny_file_open(monkeypatch, refused)
    monkeypatch.setattr(mimetypes, "knownfiles", [str(refused), str(readable), str(tmp_path / "absent.types")])
    monkeypatch.setattr(mimetypes, "_db", None, raising=False)
    use_readable_mime_database()
    assert mimetypes.knownfiles == [str(readable)]
    assert mimetypes.guess_type("photo.png")[0] == "image/png"
    assert mimetypes.guess_type("note.synthetic")[0] == "text/x-synthetic"


def test_every_known_file_being_refused_leaves_the_built_in_table(tmp_path, monkeypatch):
    refused = tmp_path / "refused.types"
    refused.write_text("text/x-refused\t\trefused\n")
    _deny_file_open(monkeypatch, refused)
    monkeypatch.setattr(mimetypes, "knownfiles", [str(refused)])
    monkeypatch.setattr(mimetypes, "_db", None, raising=False)
    use_readable_mime_database()
    assert mimetypes.knownfiles == []
    assert mimetypes.guess_type("photo.png")[0] == "image/png"
