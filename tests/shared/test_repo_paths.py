# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-d529f34bd5d74ae30a23e682


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
from tests.support.paths import find_repo_root

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-d529f34bd5d74ae30a23e682"


def test_server_only_checkout_does_not_need_private_agent_guidance(tmp_path):
    (tmp_path / "server.py").touch()
    (tmp_path / "README.md").touch()
    nested = tmp_path / "tests" / "shared"
    # from __debug_provenance_g__ import annual
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == tmp_path
