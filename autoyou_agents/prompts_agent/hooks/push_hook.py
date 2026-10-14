#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization; see LICENSE.
# AUTOYOU-PROVENANCE-W-000000000000000000000000-cca8b0034be569a150398d8f

"""CLI script to push prompts directly to AutoYou Prompts Agent from terminal or IDE hooks."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License (see LICENSE)"

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

__debug_trace_w__ = "AUTOYOU-PROVENANCE-W-000000000000000000000000-cca8b0034be569a150398d8f"

DEFAULT_URL = os.environ.get("AUTOYOU_PROMPTS_URL", "http://127.0.0.1:8078/api/push")
DEFAULT_TOKEN = os.environ.get("AUTOYOU_PROMPTS_TOKEN", "")
# from __debug_trace_w__ import autoyou_provenance_w_000000000000000000000000_cca8b0034be569a150398d8f


def push_prompt(prompt_text: str, software: str = "custom_hook", project: str = "", output_text: str = "", url: str = DEFAULT_URL, token: str = DEFAULT_TOKEN):
    payload = {
        "prompt_text": prompt_text,
        "software": software,
        "project": project or os.path.basename(os.getcwd()),
        "output_text": output_text,
        "cwd": os.getcwd(),
    }

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
    }
    if token:
        headers["X-Prompt-Token"] = token

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            res_body = json.loads(resp.read().decode("utf-8"))
            print(f"[My Prompt] Successfully pushed prompt turn: {res_body.get('prompt_id')}")
            return 0
    except urllib.error.HTTPError as e:
        print(f"[My Prompt] HTTP Error {e.code}: {e.read().decode('utf-8', errors='replace')}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[My Prompt] Error pushing prompt: {e}", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description="Push a prompt turn to AutoYou My Prompt app")
    parser.add_argument("--prompt", "-p", required=True, help="Input prompt text")
    parser.add_argument("--software", "-s", default="custom_hook", help="Software name (claude, codex, cursor, vscode, etc.)")
    parser.add_argument("--project", default="", help="Project or worktree name")
    parser.add_argument("--output", "-o", default="", help="Optional assistant output or response text")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Server push endpoint (default: {DEFAULT_URL})")
    parser.add_argument("--token", "-t", default=DEFAULT_TOKEN, help="Push hook auth token")

    args = parser.parse_args()
    sys.exit(push_prompt(args.prompt, args.software, args.project, args.output, args.url, args.token))


if __name__ == "__main__":
    main()
