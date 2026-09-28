# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

import os
import unittest

import rest_api


class RestApiSseHelperTest(unittest.TestCase):
    def test_extract_sse_events_parses_complete_blocks_and_remainder(self):
        buffer = (
            'event: message\n'
            'data: {"message":{"parts":[{"text":"Hello"}]}}\n'
            '\n'
            'data: {"message":{"parts":[{"text":"World"}]}}\n'
        )

        events, remainder = rest_api._extract_sse_events(buffer)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "message")
        self.assertIn('"Hello"', events[0]["data"])
        self.assertIn('"World"', remainder)

    def test_extract_sse_events_joins_multiple_data_lines(self):
        buffer = (
            'event: custom\n'
            'data: {"a": 1,\n'
            'data: "b": 2}\n'
            '\n'
        )

        events, remainder = rest_api._extract_sse_events(buffer)

        self.assertEqual(remainder, "")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "custom")
        self.assertEqual(events[0]["data"], '{"a": 1,\n"b": 2}')

    def test_get_run_sse_timeout_uses_env_override(self):
        original = os.environ.get("AI_AGENT_RUN_SSE_TIMEOUT_SECONDS")
        os.environ["AI_AGENT_RUN_SSE_TIMEOUT_SECONDS"] = "900"
        try:
            timeout = rest_api._get_run_sse_timeout()
            self.assertEqual(timeout.total, 900.0)
            self.assertEqual(timeout.connect, 30.0)
            self.assertEqual(timeout.sock_connect, 30.0)
            self.assertIsNone(timeout.sock_read)
        finally:
            if original is None:
                os.environ.pop("AI_AGENT_RUN_SSE_TIMEOUT_SECONDS", None)
            else:
                os.environ["AI_AGENT_RUN_SSE_TIMEOUT_SECONDS"] = original


if __name__ == "__main__":
    unittest.main()
