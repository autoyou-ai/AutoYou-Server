# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""Opt-in checks against isolated, qualified dependency wheels."""

from tests.scripts.test_patched_runtime_dependencies import probe


def test_setuptools_rejects_namespace_path_escape_and_installs_a_normal_wheel(tmp_path):
    probe(tmp_path, r'''
from pathlib import Path
import os, zipfile
from importlib.metadata import version
from setuptools.wheel import Wheel
assert version("setuptools") == "84.2.1"
root = Path(os.environ["AUTOYOU_TEST_ROOT"])

def wheel(name, namespace):
    path = root / (name + "-0.0.0-py3-none-any.whl")
    info = name + "-0.0.0.dist-info/"
    with zipfile.ZipFile(path, "w") as out:
        out.writestr(info + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        out.writestr(info + "METADATA", "Metadata-Version: 2.1\nName: " + name + "\nVersion: 0.0.0\n")
        out.writestr(info + "RECORD", "")
        out.writestr(info + "namespace_packages.txt", namespace + "\n")
    return Wheel(str(path))

normal = root / "normal"
wheel("synthetic_normal", "synthetic_namespace").install_as_egg(str(normal))
assert (normal / "synthetic_namespace/__init__.py").is_file()
outside = root / "outside"
for index, namespace in enumerate((str(outside), "../outside")):
    try:
        wheel("synthetic_escape" + str(index), namespace).install_as_egg(str(root / ("blocked" + str(index))))
    except (ValueError, OSError):
        pass
    assert not (outside / "__init__.py").exists(), "Wheel namespace escaped its installation directory"
''')


def test_filelock_preserves_exclusive_acquisition_and_timeout(tmp_path):
    probe(tmp_path, r'''
from pathlib import Path
import os
from importlib.metadata import version
from filelock import FileLock, Timeout
assert version("filelock") == "4.2.4"
path = Path(os.environ["AUTOYOU_TEST_ROOT"]) / "synthetic.lock"
with FileLock(path, timeout=1):
    try:
        FileLock(path, timeout=0).acquire()
    except Timeout:
        pass
    else:
        raise AssertionError("A competing file lock acquired an occupied resource")
with FileLock(path, timeout=1) as lock:
    assert lock.is_locked
''')


def test_litellm_sync_async_streaming_and_adk_use_a_synthetic_http_provider(tmp_path):
    probe(tmp_path, r'''
import os
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
import asyncio, json, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
import litellm
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.genai import types
assert version("litellm") == "1.100.4"
seen = []

class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        assert self.path == "/v1/chat/completions"
        assert self.headers["Authorization"] == "Bearer synthetic-api-key"
        length = int(self.headers["Content-Length"])
        assert 0 < length < 1024 * 1024
        body = json.loads(self.rfile.read(length))
        seen.append(body)
        self.send_response(200)
        if body.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for text, finish in (("synthetic ", None), ("reply", None), (None, "stop")):
                event = {"id": "chatcmpl-synthetic", "object": "chat.completion.chunk", "created": 0,
                         "model": "gpt-4o-mini", "choices": [{"index": 0,
                         "delta": {"content": text} if text else {}, "finish_reason": finish}]}
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            payload = json.dumps({"id": "chatcmpl-synthetic", "object": "chat.completion", "created": 0,
                "model": "gpt-4o-mini", "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": "synthetic reply"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
base = "http://127.0.0.1:" + str(server.server_port) + "/v1"
args = {"model": "openai/gpt-4o-mini", "api_base": base, "api_key": "synthetic-api-key", "timeout": 3,
        "messages": [{"role": "user", "content": "synthetic request"}]}

async def exercise():
    response = await litellm.acompletion(**args)
    assert response.choices[0].message.content == "synthetic reply"
    stream = await litellm.acompletion(**args, stream=True)
    chunks = [chunk async for chunk in stream]
    assert "".join(chunk.choices[0].delta.content or "" for chunk in chunks) == "synthetic reply"
    model = LiteLlm(model=args["model"], api_base=base, api_key=args["api_key"], timeout=3)
    for streaming in (False, True):
        request = LlmRequest(contents=[types.Content(role="user", parts=[types.Part(text="synthetic request")])])
        responses = [item async for item in model.generate_content_async(request, stream=streaming)]
        final = [item for item in responses if not item.partial]
        assert final and final[-1].content.parts[0].text == "synthetic reply"

try:
    assert litellm.completion(**args).choices[0].message.content == "synthetic reply"
    asyncio.run(exercise())
    assert len(seen) == 5
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
''')
