#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Build the pinned SoX codec closure and libsodium for macOS 14, without changing Homebrew."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile

if __package__:
    from .build_macos_appstore_dependencies import minimum_macos
else:
    from build_macos_appstore_dependencies import minimum_macos


CODECS = ('libsox.dylib', 'libFLAC.14.dylib', 'libmp3lame.0.dylib', 'libogg.0.dylib',
          'libpng16.16.dylib', 'libsndfile.1.dylib', 'libvorbis.0.dylib', 'libvorbisenc.2.dylib',
          'libvorbisfile.3.dylib', 'libmad.0.dylib', 'libopusfile.0.dylib', 'libopus.0.dylib', 'libmpg123.0.dylib')


def patch_sox_coreaudio(source: Path) -> None:
    path = source / 'src/coreaudio.c'
    text = path.read_text()
    before = '                  char name[256];\n'
    after = before + '                  property_size = sizeof(name);\n'
    if after in text:
        return
    if text.count(before) != 1:
        raise RuntimeError('Unexpected SoX CoreAudio device-name buffer layout')
    path.write_text(text.replace(before, after))


def download(item: dict, destination: Path) -> Path:
    if not destination.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + '.tmp')
        subprocess.run(['curl', '--fail', '--location', '--retry', '3', '--connect-timeout', '20',
                        item['url'], '--output', str(temporary)], check=True)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != item['sha256']:
            raise RuntimeError(f"Source checksum mismatch: {item['url']}")
        temporary.rename(destination)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != item['sha256']:
        raise RuntimeError(f'Cached source checksum mismatch: {destination}')
    return destination


def prepare(build_dir: Path, jobs: int) -> Path:
    pkg_config = shutil.which('pkgconf') or shutil.which('pkg-config')
    if not pkg_config:
        raise RuntimeError('pkgconf is required to build the full App Store audio runtime')
    manifest = Path(__file__).with_name('macos_appstore_native.json')
    digest = hashlib.sha256(manifest.read_bytes() + Path(__file__).read_bytes()).hexdigest()[:12]
    work = build_dir.resolve() / f'native-{digest}-{platform.machine()}'
    work.mkdir(parents=True, exist_ok=True)
    work = work.resolve()
    prefix = work / 'install'
    prefix.mkdir(parents=True, exist_ok=True)
    flags = f'-O2 -arch {platform.machine()} -mmacosx-version-min=14.0'
    env = dict(os.environ, MACOSX_DEPLOYMENT_TARGET='14.0', CFLAGS=flags, CXXFLAGS=flags,
               CPPFLAGS=f'-I{prefix}/include', LDFLAGS=f'-arch {platform.machine()} -mmacosx-version-min=14.0 -L{prefix}/lib -Wl,-rpath,{prefix}/lib',
               PKG_CONFIG_LIBDIR=f'{prefix}/lib/pkgconfig', PKG_CONFIG_PATH=f'{prefix}/lib/pkgconfig',
               PKG_CONFIG=pkg_config,
               ac_cv_prog_cc_c23='no')
    for package in json.loads(manifest.read_text())['packages']:
        name = package['name']
        marker = work / f'{name}.installed'
        if marker.exists():
            continue
        archive = download(package, work / 'downloads' / f'{name}-{package["version"]}.archive')
        unpacked = work / 'sources' / name
        if not unpacked.exists():
            unpacked.mkdir(parents=True)
            with tarfile.open(archive) as source:
                source.extractall(unpacked, filter='data')
        sources = [path for path in unpacked.iterdir() if path.is_dir()]
        if len(sources) != 1:
            raise RuntimeError(f'Expected one source directory: {unpacked}')
        source = sources[0]
        for index, patch in enumerate(package.get('patches', [])):
            applied = unpacked / f'patch-{index}.applied'
            if not applied.exists():
                patch_file = download(patch, work / 'downloads' / f'{name}-{index}.patch')
                subprocess.run(['patch', '-p1', '-i', str(patch_file)], cwd=source, check=True)
                applied.touch()
        if name == 'sox':
            # The upstream CoreAudio query must receive this buffer's size, not the device-list size.
            patch_sox_coreaudio(source)
        if name == 'libvorbis':
            configure = source / 'configure'
            configure.write_text(configure.read_text().replace(' -force_cpusubtype_ALL', ''))
        print(f'Building {name} {package["version"]} for macOS 14 with {jobs} jobs', flush=True)
        if package['build'] == 'cmake':
            build = work / 'build' / name
            subprocess.run(['cmake', '-S', str(source), '-B', str(build), '-DCMAKE_BUILD_TYPE=Release',
                            '-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0', f'-DCMAKE_OSX_ARCHITECTURES={platform.machine()}',
                            f'-DCMAKE_INSTALL_PREFIX={prefix}', f'-DCMAKE_PREFIX_PATH={prefix}',
                            '-DCMAKE_IGNORE_PREFIX_PATH=/opt/homebrew;/usr/local', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
                            '-DBUILD_SHARED_LIBS=ON', *package.get('cmake', [])], env=env, check=True)
            subprocess.run(['cmake', '--build', str(build), '--parallel', str(jobs)], env=env, check=True)
            subprocess.run(['cmake', '--install', str(build)], env=env, check=True)
        else:
            compile_env = dict(env, CFLAGS=flags + ' -Wno-incompatible-function-pointer-types -Wno-implicit-function-declaration')
            subprocess.run(['./configure', f'--prefix={prefix}', '--disable-static', '--enable-shared',
                            *package.get('configure', [])], cwd=source, env=compile_env, check=True)
            subprocess.run(['make', f'-j{jobs}'], cwd=source, env=compile_env, check=True)
            subprocess.run(['make', 'install'], cwd=source, env=compile_env, check=True)
        licenses = prefix / 'licenses' / name
        licenses.mkdir(parents=True, exist_ok=True)
        for path in source.iterdir():
            if path.is_file() and path.name.upper().startswith(('COPYING', 'LICENSE', 'AUTHORS')):
                shutil.copy2(path, licenses / path.name)
        marker.touch()
    for name in (*CODECS, 'libsodium.dylib'):
        if minimum_macos(prefix / 'lib' / name) > (14, 0):
            raise RuntimeError(f'Native dependency requires a newer macOS than 14: {name}')
    return prefix


def stage(prefix: Path, bundle: Path) -> None:
    codec_root = bundle / 'runtime_site_packages/torchaudio/lib'
    for name in CODECS:
        target = codec_root / name
        shutil.copy2(prefix / 'lib' / name, target)
        subprocess.run(['install_name_tool', '-id', f'@loader_path/{name}', str(target)], check=True)
        libraries = subprocess.check_output(['otool', '-L', str(target)], text=True).splitlines()[1:]
        for line in libraries:
            library = line.strip().split(' (', 1)[0]
            if library.startswith(('/usr/lib/', '/System/Library/')):
                continue
            dependency = Path(library).name
            if dependency not in CODECS:
                raise RuntimeError(f'Unbundled codec dependency: {library}')
            subprocess.run(['install_name_tool', '-change', library, f'@loader_path/{dependency}', str(target)], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', str(target)], check=True)
    for relative in ('shared/native/libsodium', 'runtime_modules/shared/native/libsodium'):
        target = bundle / relative / f'darwin-{platform.machine()}' / 'libsodium.dylib'
        if not target.is_file():
            raise RuntimeError(f'Missing packaged libsodium: {target}')
        shutil.copy2(prefix / 'lib/libsodium.dylib', target)
        subprocess.run(['install_name_tool', '-id', '@loader_path/libsodium.dylib', str(target)], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', str(target)], check=True)
    shutil.copytree(prefix / 'licenses', bundle / 'runtime/native-audio-licenses', dirs_exist_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--bundle-root', type=Path, required=True)
    parser.add_argument('--jobs', type=int, required=True)
    args = parser.parse_args()
    if platform.system() != 'Darwin' or args.jobs < 1:
        parser.error('Requires macOS and a positive job count')
    stage(prepare(args.build_dir, args.jobs), args.bundle_root.resolve())
    print('SoX codecs and libsodium staged for macOS 14; licenses retained.')


if __name__ == '__main__':
    main()
