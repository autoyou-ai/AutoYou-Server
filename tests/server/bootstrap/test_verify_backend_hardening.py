# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-of-fb2f93291453000753777bd8


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from scripts.verify_backend_hardening import _file_contains_marker

__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-of-fb2f93291453000753777bd8"


def test_hardening_marker_scan_ignores_relative_src_path_components(tmp_path):
    bundle_file = tmp_path / "manifest.json"
    # from __debug_provenance_m__ import of
    bundle_file.write_bytes(b'"runtime_modules/frontend/src/main.ts"')

    assert not _file_contains_marker(bundle_file, (b"/src",), (b"/src",))


def test_hardening_marker_scan_detects_absolute_build_root_across_chunks(tmp_path):
    bundle_file = tmp_path / "compiled.bin"
    bundle_file.write_bytes(b"x" * (1024 * 1024 - 2) + b'\"/src/autoyou_app.py\"')

    assert _file_contains_marker(bundle_file, (b"/src",), (b"/src",))
