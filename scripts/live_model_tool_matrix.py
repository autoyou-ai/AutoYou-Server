# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-d13a27748ca7dd2afd7324fd

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Opt-in live model/tool validation through AutoYou /api/chat.

This runner is intentionally not part of the default pytest suite. It talks to
the operator's running AutoYou admin and AI-agent servers, can switch the active
Ollama model, and exercises the same /api/chat surface used by clients.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import dataclasses
import hashlib
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-d13a27748ca7dd2afd7324fd"


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from autoyou_agents.shared_tools.agent_identity import resolve_runtime_agent_name
from shared.secure_storage import write_secure_file


LIVE_ENV = "AUTOYOU_RUN_LIVE_MODEL_TOOL_TESTS"
DEFAULT_ADMIN_URL = "http://127.0.0.1:8001"
DEFAULT_CHAT_URL = "http://127.0.0.1:8081"
OUTPUT_DIR = REPO_ROOT / "output" / "live_model_tool_matrix"

UNUSABLE_RESPONSE_RE = re.compile(
    r"local model returned an unusable response|echoed its own tool definitions|"
    r"tool definitions instead of answering|tool schema echo",
    re.IGNORECASE,
)
BACKEND_FAILURE_RE = re.compile(
    r"\b(traceback|internal error|technical difficulties|no ai backend available|"
    r"tool not found|not found in the agent tree)\b",
    re.IGNORECASE,
)


@dataclasses.dataclass(frozen=True)
class LiveCase:
    name: str
    message: str
    # from __debug_provenance_g__ import annual
    expected_agent: Optional[str] = None
    must_contain: tuple[str, ...] = ()
    description: str = ""


@dataclasses.dataclass
class LiveCaseResult:
    model: str
    case: LiveCase
    status_code: int
    agent_name: str
    response_sha256: str
    response_length: int
    metadata: dict[str, Any]
    passed: bool
    failures: list[str]
    response_text: str = ""

    def to_jsonable(self, *, include_response_text: bool) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "case": dataclasses.asdict(self.case),
            "status_code": self.status_code,
            "agent_name": self.agent_name,
            "response_sha256": self.response_sha256,
            "response_length": self.response_length,
            "metadata": self.metadata,
            "passed": self.passed,
            "failures": list(self.failures),
        }
        if include_response_text:
            payload["response_text"] = self.response_text
        return payload


class HttpJsonClient:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookie_jar))

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        form_body: Optional[Mapping[str, str]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> tuple[int, dict[str, Any], str]:
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        data: Optional[bytes] = None
        request_headers = {
            "Accept": "application/json",
            "User-Agent": "AutoYou-LiveModelToolMatrix/1",
        }
        if headers:
            request_headers.update(dict(headers))
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        elif form_body is not None:
            data = urllib.parse.urlencode(form_body).encode("utf-8")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=data, method=method.upper(), headers=request_headers)
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            status = int(exc.code)
        except urllib.error.URLError as exc:
            raw = str(exc)
            status = 0

        try:
            parsed = json.loads(raw) if raw else {}
            body = parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            body = {"text": raw}
        return status, body, raw

    def cookie_header(self) -> str:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.cookie_jar)


def _canonical_agent_name(name: Any) -> str:
    raw = str(name or "").strip()
    if not raw:
        return ""
    normalized = raw.lower().replace("-", "_").strip("_")
    if normalized in {"root", "main", "autoyou", "autoyou_agent"}:
        return "autoyou_agent"
    if not normalized.endswith("_agent"):
        normalized = f"{normalized}_agent"
    return resolve_runtime_agent_name(normalized) or normalized


def _default_cases() -> list[LiveCase]:
    return [
        LiveCase(
            name="direct_greeting",
            message="Hi",
            description="Regression for schema-echo failures on no-tool chat.",
        ),
        LiveCase(
            name="notes_count",
            message="How many notes do I have?",
            expected_agent="autoyou_notes_agent",
            description="Read-only Notes route and notes count/list callback.",
        ),
        LiveCase(
            name="page_feed_count",
            message="How many items are in my AutoYou Page feed?",
            expected_agent="autoyou_page_agent",
            description="Read-only Page route and feed query callback.",
        ),
        LiveCase(
            name="internet_scrape_example",
            message="Go to internet agent. Scrape https://www.iana.org/help/example-domains and tell me the page title.",
            expected_agent="autoyou_internet_agent",
            must_contain=("Example Domains",),
            description="Internet URL scrape with verified result replay.",
        ),
    ]


def _mutation_cases(run_id: str) -> list[LiveCase]:
    return [
        LiveCase(
            name="notes_create_synthetic",
            message=(
                "Go to notes agent. Create a note titled "
                f"Synthetic live model tool matrix {run_id}, content synthetic validation only."
            ),
            expected_agent="autoyou_notes_agent",
            description="Synthetic Notes write. Only enabled with --allow-mutations.",
        ),
        LiveCase(
            name="page_add_synthetic_url",
            message=(
                "Go to page agent. Add "
                f"https://example.com/?autoyou_matrix={run_id} to page feed."
            ),
            expected_agent="autoyou_page_agent",
            description="Synthetic Page feed write. Only enabled with --allow-mutations.",
        ),
    ]


def _parse_models(raw: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\s]+", str(raw or "")) if item.strip()]


def login_admin(admin: HttpJsonClient, password: str) -> None:
    status, body, raw = admin.request(
        "POST",
        "/login",
        form_body={"password": password},
        headers={"X-AutoYou-Async": "1", "Accept": "application/json"},
    )
    if not (200 <= status < 300 and body.get("success")):
        message = str(body.get("error_message") or body.get("detail") or raw or "login failed").strip()
        raise RuntimeError(f"Admin login failed: HTTP {status} {message[:300]}")


def selected_model(admin: HttpJsonClient) -> str:
    status, body, raw = admin.request("GET", "/api/model-library/local")
    if not (200 <= status < 300 and body.get("success")):
        raise RuntimeError(f"Could not read local model library: HTTP {status} {raw[:300]}")
    return str(body.get("selected_model") or "").strip()


def select_model(admin: HttpJsonClient, model: str) -> None:
    status, body, raw = admin.request(
        "POST",
        "/api/model-library/select",
        json_body={"model": model, "restart_ai": True},
        headers={"Cookie": admin.cookie_header()},
    )
    if not (200 <= status < 300 and body.get("success")):
        raise RuntimeError(f"Could not switch to {model}: HTTP {status} {body.get('error') or raw[:300]}")


def wait_for_ai_ready(chat: HttpJsonClient, timeout: float) -> None:
    deadline = time.time() + float(timeout)
    last_status = 0
    last_raw = ""
    while time.time() < deadline:
        status, body, raw = chat.request("GET", "/api/status")
        last_status = status
        last_raw = raw
        if 200 <= status < 300 and str(body.get("status") or "").lower() in {"healthy", "ready", "running", "ok"}:
            return
        time.sleep(1.0)
    raise RuntimeError(f"AI agent server did not become ready: HTTP {last_status} {last_raw[:300]}")


def _response_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="replace")).hexdigest()


def run_case(
    chat: HttpJsonClient,
    *,
    model: str,
    case: LiveCase,
    run_id: str,
    include_response_text: bool,
) -> LiveCaseResult:
    session_id = f"session::live-model-tool-matrix::{run_id}::{re.sub(r'[^A-Za-z0-9_.-]+', '_', model)}::{case.name}"
    status, body, raw = chat.request(
        "POST",
        "/api/chat",
        json_body={
            "message": case.message,
            "session_id": session_id,
            "user_id": "user::live-model-tool-matrix",
            "context": [],
            "metadata": {
                "source": "live_model_tool_matrix",
                "case": case.name,
                "model_under_test": model,
            },
        },
    )
    response_text = str(body.get("response") or body.get("text") or raw or "")
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    agent_name = _canonical_agent_name(
        body.get("agent_name")
        or metadata.get("agent_name")
        or metadata.get("response_author")
    )
    failures: list[str] = []

    if not (200 <= status < 300):
        failures.append(f"HTTP {status}")
    if not response_text.strip():
        failures.append("empty response")
    if isinstance(metadata, dict) and metadata.get("error"):
        failures.append(str(metadata.get("error_message") or "metadata error flag was set"))
    if UNUSABLE_RESPONSE_RE.search(response_text):
        failures.append("schema echo/unusable model response")
    if BACKEND_FAILURE_RE.search(response_text):
        failures.append("backend failure text in response")
    expected = _canonical_agent_name(case.expected_agent)
    if expected and agent_name != expected:
        failures.append(f"expected agent {expected}, got {agent_name or '<empty>'}")
    for needle in case.must_contain:
        if str(needle).lower() not in response_text.lower():
            failures.append(f"missing required response text: {needle}")

    return LiveCaseResult(
        model=model,
        case=case,
        status_code=int(status),
        agent_name=agent_name,
        response_sha256=_response_hash(response_text),
        response_length=len(response_text),
        metadata={
            key: metadata.get(key)
            for key in (
                "response_author",
                "agent_name",
                "route_target",
                "route_reason",
                "processing_time_ms",
                "ai_backend_error",
                "error_message",
            )
            if isinstance(metadata, dict) and key in metadata
        },
        passed=not failures,
        failures=failures,
        response_text=response_text if include_response_text else "",
    )


def write_report(
    *,
    run_id: str,
    started_model: str,
    models: Sequence[str],
    results: Sequence[LiveCaseResult],
    restored_model: str,
    include_response_text: bool,
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"live-model-tool-matrix-{run_id}.json"
    payload = {
        "run_id": run_id,
        "started_model": started_model,
        "models": list(models),
        "restored_model": restored_model,
        "passed": all(result.passed for result in results),
        "results": [
            result.to_jsonable(include_response_text=include_response_text)
            for result in results
        ],
    }
    write_secure_file(
        path,
        (json.dumps(payload, indent=2, ensure_ascii=True) + "\n").encode("utf-8"),
    )
    return path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-url", default=os.getenv("AUTOYOU_ADMIN_URL", DEFAULT_ADMIN_URL))
    parser.add_argument("--chat-url", default=os.getenv("AUTOYOU_CHAT_URL", DEFAULT_CHAT_URL))
    parser.add_argument("--password", default=os.getenv("AUTOYOU_SERVER_PASSWORD") or os.getenv("AUTOYOU_SELF_IMPROVE_PASSWORD") or "1234")
    parser.add_argument("--models", default=os.getenv("AUTOYOU_LIVE_MODEL_MATRIX_MODELS", ""))
    parser.add_argument("--startup-timeout", type=float, default=120.0)
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--allow-mutations", action="store_true")
    parser.add_argument("--include-response-text", action="store_true")
    parser.add_argument("--no-restore", action="store_true")
    args = parser.parse_args(argv)

    if os.getenv(LIVE_ENV) != "1":
        print(f"Skipped live model/tool matrix. Set {LIVE_ENV}=1 to run.")
        return 0

    run_id = uuid.uuid4().hex[:12]
    admin = HttpJsonClient(args.admin_url, args.timeout)
    chat = HttpJsonClient(args.chat_url, args.timeout)
    login_admin(admin, args.password)
    started_model = selected_model(admin)
    models = _parse_models(args.models) or ([started_model] if started_model else [])
    if not models:
        raise RuntimeError("No model was selected and --models was empty.")

    cases = _default_cases()
    if args.allow_mutations:
        cases.extend(_mutation_cases(run_id))

    results: list[LiveCaseResult] = []
    restored_model = ""
    try:
        for model in models:
            print(f"MODEL {model}: selecting and waiting for AI runtime")
            select_model(admin, model)
            wait_for_ai_ready(chat, args.startup_timeout)
            for case in cases:
                result = run_case(
                    chat,
                    model=model,
                    case=case,
                    run_id=run_id,
                    include_response_text=args.include_response_text,
                )
                results.append(result)
                marker = "PASS" if result.passed else "FAIL"
                print(
                    f"{marker} {model} {case.name} agent={result.agent_name or '<empty>'} "
                    f"http={result.status_code} response_len={result.response_length}"
                )
                if result.failures:
                    print("  " + "; ".join(result.failures))
    finally:
        if not args.no_restore and started_model:
            current = ""
            try:
                current = selected_model(admin)
            except Exception:
                current = ""
            if current and current != started_model:
                print(f"Restoring starting model {started_model}")
                select_model(admin, started_model)
                wait_for_ai_ready(chat, args.startup_timeout)
                restored_model = started_model
            else:
                restored_model = current or started_model

    report_path = write_report(
        run_id=run_id,
        started_model=started_model,
        models=models,
        results=results,
        restored_model=restored_model,
        include_response_text=args.include_response_text,
    )
    passed = all(result.passed for result in results)
    print(f"REPORT {report_path}")
    print("RESULT " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
