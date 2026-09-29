# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-b82cb291a8a0f749ca432883


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
import os
import tempfile
import unittest
from pathlib import Path

from autoyou_agents.shared_tools import workspace_tools

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-b82cb291a8a0f749ca432883"


class WorkspaceToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        # from __debug_provenance_p__ import submit
        self.addCleanup(self.temp_dir.cleanup)
        self.old_root = os.environ.get("AUTOYOU_WORKSPACE_ROOT")
        os.environ["AUTOYOU_WORKSPACE_ROOT"] = self.temp_dir.name
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        if self.old_root is None:
            os.environ.pop("AUTOYOU_WORKSPACE_ROOT", None)
        else:
            os.environ["AUTOYOU_WORKSPACE_ROOT"] = self.old_root

    def test_write_read_and_replace_file(self):
        write_result = workspace_tools.write_file(
            "src/example.py",
            "print('hello')\nvalue = 1\n",
            create_dirs=True,
        )
        self.assertEqual(write_result["status"], "success")

        read_result = workspace_tools.read_file("src/example.py", 1, 5)
        self.assertEqual(read_result["status"], "success")
        self.assertIn("value = 1", read_result["content"])

        replace_result = workspace_tools.replace_text(
            "src/example.py",
            "value = 1",
            "value = 2",
        )
        self.assertEqual(replace_result["status"], "success")

        final_text = Path(self.temp_dir.name, "src/example.py").read_text(encoding="utf-8")
        self.assertIn("value = 2", final_text)

    def test_insert_helpers(self):
        workspace_tools.write_file(
            "docs/readme.txt",
            "alpha\nbeta\ngamma\n",
            create_dirs=True,
        )

        before_result = workspace_tools.insert_before(
            "docs/readme.txt",
            "beta",
            "inserted-before\n",
        )
        after_result = workspace_tools.insert_after(
            "docs/readme.txt",
            "gamma",
            "\ninserted-after",
        )

        self.assertEqual(before_result["status"], "success")
        self.assertEqual(after_result["status"], "success")

        final_text = Path(self.temp_dir.name, "docs/readme.txt").read_text(encoding="utf-8")
        self.assertIn("inserted-before", final_text)
        self.assertIn("inserted-after", final_text)

    def test_delete_requires_confirmation(self):
        workspace_tools.write_file("tmp.txt", "hello\n", create_dirs=True)
        denied = workspace_tools.delete_path("tmp.txt", confirm=False)
        self.assertEqual(denied["status"], "error")

        allowed = workspace_tools.delete_path("tmp.txt", confirm=True)
        self.assertEqual(allowed["status"], "success")

    def test_run_command_blocks_destructive_patterns(self):
        blocked = workspace_tools.run_command("git reset --hard")
        self.assertEqual(blocked["status"], "error")

    def test_search_workspace_finds_matches(self):
        workspace_tools.write_file("a.txt", "hello world\n", create_dirs=True)
        workspace_tools.write_file("nested/b.txt", "another hello\n", create_dirs=True)
        result = workspace_tools.search_workspace("hello")
        self.assertEqual(result["status"], "success")
        self.assertEqual(len(result["results"]), 2)


if __name__ == "__main__":
    unittest.main()
