# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-515f4c8280a2743a139a0f43


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-564758726b4c66755a7a6b38-515f4c8280a2743a139a0f43"

from tests.support.paths import find_repo_root


def test_server_only_checkout_does_not_need_private_agent_guidance(tmp_path):
    (tmp_path / "server.py").touch()
    (tmp_path / "README.md").touch()
    nested = tmp_path / "tests" / "shared"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == tmp_path
