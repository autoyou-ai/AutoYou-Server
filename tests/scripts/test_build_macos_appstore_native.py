from pathlib import Path
import hashlib
import json

import pytest

from scripts import build_macos_appstore_native as native


def test_cached_source_requires_the_exact_pinned_checksum(tmp_path):
    archive = tmp_path / 'synthetic.archive'
    archive.write_bytes(b'synthetic source')
    item = {'url': 'https://example.test/synthetic.tar.gz', 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    assert native.download(item, archive) == archive
    archive.write_bytes(b'corrupt source')
    with pytest.raises(RuntimeError, match='checksum mismatch'):
        native.download(item, archive)


def test_source_build_requires_pkgconf_before_writing_build_state(tmp_path, monkeypatch):
    monkeypatch.setattr(native.shutil, 'which', lambda name: None)
    with pytest.raises(RuntimeError, match='pkgconf is required'):
        native.prepare(tmp_path / 'build', 10)
    assert not (tmp_path / 'build').exists()


def test_native_manifest_pins_every_source_and_preserves_the_existing_codecs():
    manifest = json.loads(Path(native.__file__).with_name('macos_appstore_native.json').read_text())
    packages = manifest['packages']
    assert len(packages) == 12
    assert {item['name'] for item in packages} == {
        'libogg', 'libvorbis', 'flac', 'mpg123', 'lame', 'opus', 'opusfile', 'libpng', 'mad', 'libsndfile', 'sox', 'libsodium'}
    for item in packages:
        assert item['url'].startswith('https://') and len(item['sha256']) == 64
    assert len(next(item for item in packages if item['name'] == 'sox')['patches']) == 1
    assert len(native.CODECS) == 13


def test_coreaudio_device_buffer_patch_is_idempotent_and_fails_on_layout_drift(tmp_path):
    source = tmp_path / 'src/coreaudio.c'
    source.parent.mkdir()
    source.write_text('                  char name[256];\n')
    native.patch_sox_coreaudio(tmp_path)
    expected = source.read_text()
    assert 'property_size = sizeof(name);' in expected
    native.patch_sox_coreaudio(tmp_path)
    assert source.read_text() == expected
    source.write_text('unknown upstream source')
    with pytest.raises(RuntimeError, match='Unexpected SoX CoreAudio'):
        native.patch_sox_coreaudio(tmp_path)


def test_staging_fails_for_an_unbundled_codec_dependency(tmp_path, monkeypatch):
    prefix, bundle = tmp_path / 'prefix', tmp_path / 'bundle'
    (prefix / 'lib').mkdir(parents=True)
    (prefix / 'lib/libsox.dylib').write_bytes(b'synthetic codec')
    (bundle / 'runtime_site_packages/torchaudio/lib').mkdir(parents=True)
    monkeypatch.setattr(native, 'CODECS', ('libsox.dylib',))
    monkeypatch.setattr(native.subprocess, 'run', lambda *args, **kwargs: None)
    monkeypatch.setattr(native.subprocess, 'check_output', lambda *args, **kwargs:
                        'synthetic codec:\n /unexpected/libmissing.dylib (compatibility version 1.0.0)\n')
    with pytest.raises(RuntimeError, match='Unbundled codec dependency'):
        native.stage(prefix, bundle)
