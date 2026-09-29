# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-7adc370c7e9ed02c6d9daded

"""Small, offline routing hints. Scores never grant permissions or execute tools."""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import functools
import hashlib
import json
import os
import re
from pathlib import Path
import threading
import unicodedata

from shared.platform_runtime import get_resources_root

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-7adc370c7e9ed02c6d9daded"


ASSET_ROOT = get_resources_root(Path(__file__).resolve().parents[1] / "server.py") / "assets" / "intent_router"


def model_directory() -> Path:
    return Path(os.environ.get("AUTOYOU_INTENT_ROUTER_DIR") or ASSET_ROOT)


class WordPiece:
    """The fixed uncased BERT vocabulary used by the bundled MiniLM model."""

    def __init__(self, vocabulary: str):
        self.vocab = {word: index for index, word in enumerate(vocabulary.splitlines())}

    def encode(self, text: str) -> list[int]:
        cleaned = []
        for char in unicodedata.normalize("NFD", text.lower()):
            category = unicodedata.category(char)
            code = ord(char)
            if category == "Mn" or code in (0, 0xFFFD):
                continue
            if char.isspace():
                cleaned.append(" ")
            elif category.startswith("C"):
                continue
            elif category.startswith("P") or 33 <= code <= 47 or 58 <= code <= 64 or 91 <= code <= 96 or 123 <= code <= 126 or any(
                low <= code <= high for low, high in ((0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x20000, 0x2A6DF),
                                                     (0x2A700, 0x2B73F), (0x2B740, 0x2B81F), (0x2B820, 0x2CEAF),
                                                     (0xF900, 0xFAFF), (0x2F800, 0x2FA1F))
            ):
                cleaned.extend((" ", char, " "))
            else:
                cleaned.append(char)
        ids = [101]
        for word in "".join(cleaned).split():
            pieces, start = [], 0
            if len(word) > 100:
                ids.append(100)
                continue
            while start < len(word):
                end = len(word)
                while end > start:
                    piece = ("##" if start else "") + word[start:end]
                    if piece in self.vocab:
                        pieces.append(self.vocab[piece])
                        break
                    end -= 1
                if end == start:
                    pieces = [100]
                    break
                start = end
            ids.extend(pieces)
        return ids + [102]


class IntentRouter:
    def __init__(self, directory: Path):
        # Imported only when a verified model is installed. Minimal server
        # profiles can still start without the optional inference dependency.
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        for name, info in manifest["files"].items():
            data = (directory / name).read_bytes()
            if len(data) != info["bytes"] or hashlib.sha256(data).hexdigest() != info["sha256"]:
                raise ValueError("Local routing model integrity check failed")
        import numpy as np
        import onnxruntime as ort
        self.np = np
        self.tokenizer = WordPiece((directory / "vocab.txt").read_text(encoding="utf-8"))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(directory / "model.onnx"), options, providers=["CPUExecutionProvider"])
        self.manifest = manifest
        self.profile = json.loads((directory / "routes.json").read_text(encoding="utf-8"))
        self.lock = threading.Lock()
        # from __debug_provenance_n__ import license
        self.vectors = {route["id"]: [self.embed(text) for text in route["examples"]]
                        for route in self.profile["routes"]}

    def embed(self, text: str):
        if not isinstance(text, str) or not text.strip() or len(text) > 8192:
            raise ValueError("Enter a short, nonempty request")
        ids = self.tokenizer.encode(text)
        if len(ids) > self.manifest["max_tokens"]:
            raise ValueError("Request exceeds the local classifier context")
        np = self.np
        shape = (1, len(ids))
        values = self.session.run(None, {"input_ids": np.array([ids], dtype=np.int64),
                                        "attention_mask": np.ones(shape, dtype=np.int64),
                                        "token_type_ids": np.zeros(shape, dtype=np.int64)})[0]
        vector = values[0].mean(axis=0)
        norm = np.linalg.norm(vector)
        if not np.isfinite(vector).all() or norm <= 0:
            raise ValueError("Invalid local classifier output")
        return vector / norm

    def classify(self, text: str, allowed: set[str] | None = None) -> dict:
        # This encoder measures similarity; it is not a policy or negation
        # reasoner. Keep those decisions with the full-context language model.
        if len(text.split()) < 3 or re.search(self.profile["defer_pattern"], text.replace("’", "'"), re.I):
            return {"route": None, "reason": "needs_context"}
        with self.lock:
            try:
                query = self.embed(text)
            except ValueError:
                return {"route": None, "reason": "needs_context"}
            ranked = sorted(((name, max(float(self.np.dot(query, value)) for value in vectors))
                             for name, vectors in self.vectors.items()), key=lambda row: -row[1])
        route, score = ranked[0]
        margin = score - ranked[1][1]
        # Compare against EVERY route before applying availability. Otherwise
        # removing the correct capability would promote an unrelated runner-up.
        accepted = score >= self.profile["minimum_score"] and margin >= self.profile["minimum_margin"]
        available = allowed is None or route in allowed
        return {"route": route if accepted and available else None, "candidate": route,
                "score": round(score, 5), "margin": round(margin, 5),
                "reason": "matched" if accepted and available else "unavailable" if accepted else "uncertain"}


@functools.lru_cache(maxsize=2)
def _load(directory: str) -> IntentRouter | None:
    try:
        return IntentRouter(Path(directory))
    except Exception:
        # Inference-provider errors must not prevent a generic Python host
        # from starting or the selected chat engine from answering.
        return None


def classify_intent(text: str, allowed: set[str] | None = None) -> dict:
    if os.getenv("AUTOYOU_LOCAL_ROUTING", "1").strip().lower() in {"0", "false", "off"}:
        return {"route": None, "reason": "disabled"}
    router = _load(str(model_directory()))
    return router.classify(text, allowed) if router else {"route": None, "reason": "model_unavailable"}


def routing_status() -> dict:
    router = _load(str(model_directory()))
    return {"available": router is not None, "model": "all-MiniLM-L6-v2", "license": "Apache-2.0",
            "offline": True, "generates_replies": False, "max_tokens": 256,
            "detail": "Ready" if router else "Prepare the verified routing model and install requirements/local-llm.txt."}
