"""Synthetic runtime signatures must not hide changes to executable code."""
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import pytest

from shared.macos_runtime_integrity import hash_macos_runtime_file


def signed_image(signature: bytes, *, endian="<", payload=b"synthetic code") -> bytes:
    start = 32 + 72 + 16 + len(payload)
    header = struct.pack(endian + "8I", 0xFEEDFACF, 0x100000C, 0, 8, 2, 88, 0, 0)
    segment = struct.pack(endian + "II16sQQQQiiII", 0x19, 72, b"__LINKEDIT",
                          4096, len(signature) + 4096, start, len(signature), 1, 1, 0, 0)
    command = struct.pack(endian + "4I", 0x1D, 16, start, len(signature))
    return header + segment + command + payload + signature


@pytest.mark.parametrize("endian", ["<", ">"])
def test_signature_replacement_preserves_code_hash_and_code_changes_fail(tmp_path, endian):
    module = tmp_path / "synthetic.so"
    module.write_bytes(signed_image(b"first signature", endian=endian))
    expected = hash_macos_runtime_file(module)
    module.write_bytes(signed_image(b"replacement signature with a different length", endian=endian))
    assert hash_macos_runtime_file(module) == expected
    module.write_bytes(signed_image(b"first signature", endian=endian, payload=b"modified code"))
    assert hash_macos_runtime_file(module) != expected
    module.write_bytes(signed_image(b"first signature", endian=endian) + b"extra payload")
    assert hash_macos_runtime_file(module) != expected


def test_non_macho_assets_keep_sha256_and_bad_signature_ranges_fail(tmp_path):
    asset = tmp_path / "synthetic.asset"
    asset.write_bytes(b"ordinary runtime data")
    assert hash_macos_runtime_file(asset) == hashlib.sha256(asset.read_bytes()).hexdigest()
    image = bytearray(signed_image(b"signature"))
    struct.pack_into("<I", image, 32 + 72 + 8, 0)
    asset.write_bytes(image)
    with pytest.raises(ValueError, match="signature range"):
        hash_macos_runtime_file(asset)
    asset.write_bytes(bytes.fromhex("cafebabe") + bytes(32))
    with pytest.raises(ValueError, match="Unsupported Mach-O"):
        hash_macos_runtime_file(asset)


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("cc"), reason="requires macOS tools")
def test_real_apple_resigning_preserves_code_hash(tmp_path):
    source = tmp_path / "synthetic.c"
    source.write_text("int synthetic_value(void) { return 42; }\n")
    module = tmp_path / "synthetic.dylib"
    subprocess.run(["cc", "-dynamiclib", str(source), "-o", str(module)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", "--identifier",
                    "com.example.synthetic.first", str(module)], check=True)
    expected = hash_macos_runtime_file(module)
    original = hashlib.sha256(module.read_bytes()).hexdigest()
    subprocess.run(["codesign", "--force", "--sign", "-", "--identifier",
                    "com.example.synthetic.replacement.with.a.longer.identifier", str(module)], check=True)
    assert hashlib.sha256(module.read_bytes()).hexdigest() != original
    assert hash_macos_runtime_file(module) == expected
